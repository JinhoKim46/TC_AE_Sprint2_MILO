# Protocol M7-JUDGE: RAG evaluation with an LLM judge, checked against labels

| Field | Value |
|---|---|
| Protocol id | `m7-judge` |
| Status | committed before any result file (D83); the judge prompts are committed after the labels (D26, SPEC 10.5) |
| Date | 2026-10-22 |
| Owning milestone | M7 (evaluation) |
| Implements | SPEC 10.5, D24, D26, D27, D56, D83, D126; HARNESS rules 1, 6, 14 |
| Produces | `evals/rag/**`, `evals/results/m7/generation_dev.json` (live), `evals/results/m7/judge_dev.json` (live), `evals/results/m7/rag_dry.json` (offline fixture, never a MILO result) |
| Measured calls | product answers on 30 dev questions, then two judge models over the same 30; estimate and cap in the last section |

## Question

Can an LLM judge, run on OpenRouter, score MILO's answers for faithfulness, answer relevance and
context precision closely enough to a separately written set of labels that its scores can be reported
as evidence (D27, hard claim "RAG evaluation")? Two judge models are tried; each is validated on its own.

## Data

1. **Sample.** 30 questions from the 60 dev rows (D26, "Updated counts"), loaded only through
   `evals/common.py` (`load_gold("dev")`; the test split is never read here). Stratified by `type`
   with proportional allocation (paraphrase 18, identifier 6, unanswerable 6), drawn with
   `random.Random(26)` over the sorted qids of each stratum, then sorted. The sampler writes
   `evals/rag/sample_dev.json` (qids, types, seed, SHA-256 of the gold file) and refuses a row whose
   split is not `dev`. The sample is committed before generation.
2. **Product output.** For each sampled question, the product pipeline (`milo.app.build_app`, contract
   mode, dense top 5, `min_score` 0.30 unless the integrator has adopted an M2 arm, in which case the
   configuration is recorded in the result) answers once with the product chat model (D4, slug to verify
   at run time). Stored per question: the question, the retrieved contexts (chunk id, doc id, score, text),
   the answer, whether the product abstained (no contexts), tokens and cost. This is
   `evals/results/m7/generation_dev.json`, produced in the live batch before the labelling.
3. **Labels.** A separate labelling agent (D26) reads the generation file and writes
   `evals/rag/labels.jsonl`, one row per sampled qid, before any judge prompt exists. It may read the
   gold row of the question (reference answer and quote) because the labels are ground truth for the
   judge, not for the product. It never sees a judge reply. The label schema is `evals/rag/schema.py`;
   `evals/rag/labels.template.jsonl` lists the 30 qids with empty labels. Fields per row:
   `qid`, `faithfulness` (0 or 1), `relevance` (0, 1 or 2), `context_relevance` (a list of 0 or 1, one per
   retrieved context in the stored order; `[]` when the product retrieved nothing), `labeller`, optional `note`.
   The validator rejects a missing, duplicate or extra qid, a value outside the allowed set, a null, a
   `context_relevance` of the wrong length, and an unknown field.
4. **Judges.** Two OpenRouter models other than the product model, taken from the E02 comparison
   (`e02-chat-compare.md`): the two lowest-cost candidates that pass the E02 decision rule; if fewer than
   two pass, the reference `gpt-4.1-mini` and then the next-cheapest candidate fill the places. Slugs and
   listed prices: to verify at run time against the catalogue; pinned in the result header. A judge never
   scores its own product answers because the product model is not a judge candidate.
5. **Prompts.** `evals/rag/prompts/{faithfulness,relevance,context_precision}.v1.md`. Today they are
   placeholders that say "written after labels.jsonl". The runner refuses a live judge run while a prompt
   is a placeholder, while `labels.jsonl` is missing or invalid, or while git does not show the first
   commit of `labels.jsonl` as a proper ancestor of the last commit of every prompt file. That makes the
   D26 order checkable by git, for the runner and for the verifier.

## Procedure

Metric definitions (the labelling agent and the judge prompts use the same ones):

| Metric | Unit | Scale | 1 / 2 means |
|---|---|---|---|
| faithfulness | per answer | binary | 1: every factual claim in the answer is supported by the retrieved contexts. 0: at least one claim is not. An abstention ("I do not know") makes no claim and scores 1 |
| answer relevance | per answer | ordinal 0, 1, 2 | 2: answers what was asked. 1: partly answers, or declines honestly. 0: off topic or ignores the question. Judged from question and answer only |
| context precision | per context | binary per context | 1: the context holds information needed to answer the question. 0: it does not. Per question: the share of 1s. Undefined (excluded) when nothing was retrieved |

Steps, in this order (the order is a rule, not advice):

1. `run_rag_eval.py --sample` writes the sample. Committed.
2. Live batch, generation: `MILO_LIVE=1 ... --generate`. Writes `generation_dev.json`. Committed.
3. `run_rag_eval.py --make-template --generation evals/results/m7/generation_dev.json` rewrites the
   template with the right number of context slots. The labelling agent fills
   `evals/rag/labels.jsonl`; `run_rag_eval.py --check-labels` must pass. Committed alone.
4. The judge prompts are written and committed (a later commit than step 3).
5. Live batch, judge: `MILO_LIVE=1 ... --judge`. Three calls per question and judge (one per metric;
   context precision returns one verdict per context), temperature 0, `reasoning` disabled, through
   `milo.llm.cloud` and `llm/retry.py`. Reply contract: a single JSON object, `{"score": 0|1, "reason": "..."}`
   for faithfulness, `{"score": 0|1|2, ...}` for relevance, `{"scores": [0|1, ...], ...}` for context
   precision with one entry per context. A reply that is not that contract is stored raw and counted
   `unparsed`; no second ask. The result `judge_dev.json` stores, per row: the question, contexts, answer,
   labels, and per judge and metric the raw reply, parsed score, tokens and cost (D56).
6. Agreement with the labels, per judge and metric: Cohen's kappa (unweighted for the binary metrics,
   quadratic-weighted for relevance), raw agreement, label prevalence, judge mean and label mean, and a
   bootstrap 95 percent CI (2000 resamples, seed 0, D24) that resamples **questions** (context precision
   pools the contexts of the resampled questions). Also the kappa between the two judges, and the mean
   metric values per judge and per label set, by question type.
7. `--replay` recomputes every number from the stored rows and must match exactly. The offline result
   `rag_dry.json` is also regenerated from the fixture (`evals/rag/fixtures/rag_dry_fixture.json`: invented
   questions, contexts, answers, labels and scripted judge replies) and compared row by row. Live reruns
   of the judge are reported, not failed (SPEC 10.3).

## Decision rule

Fixed here, before any result. For one judge and one metric, **validated** means: kappa point estimate at
least 0.60 and the CI lower bound at least 0.30 (so that a point estimate above 0.60 on 30 questions with an
interval reaching zero does not count). A judge is **usable** when all three metrics are validated and at most
5 percent of its replies were unparsed.

- At least one usable judge: the M7 report states the product's faithfulness, relevance and context
  precision from that judge (the better kappa on ties of usability), with the other judge's numbers beside
  them, and D27's hard claim stands.
- No usable judge: the report gives every kappa and CI, states that no judge was validated, shows the judged
  numbers only as "not validated", and does not claim RAG evaluation as met. The labels alone still give the
  product's measured values (the label means), which are reported as the labelled-sample result.
- A metric that is validated for one judge only is reported for that judge only, labelled so.
- Kappa is undefined when one label value fills the whole column; that counts as not validated and the
  report says so. When the minority label has fewer than 5 items the report flags the kappa as low prevalence.

## Limits

- 30 questions give wide intervals; the thresholds above are absolute and conservative on purpose. The
  sample has 6 unanswerable questions, which are mostly abstentions, so faithfulness is near 1 by
  construction on that stratum.
- The labelling agent is one labeller (a model session, not a person), so kappa measures agreement with one
  labeller, not with a human standard. Its rubric is the table above and is stored in `schema.py`.
- The labels were written after the product answers were fixed; the labeller could be influenced by them. The
  judge prompts are written after the labels and may be tuned to them, which inflates kappa on this sample; the
  report says so, and no prompt is changed after the first judge run.
- Judge prompts and the product prompt are stand-ins for a wider set; one run per judge at temperature 0 is
  not bit-reproducible, so a live rerun is reported, not failed.
- Context precision is binary by context, not graded by rank position.
- Slugs, prices and the E02 ranking come from the catalogue and the E02 result on the run day.
- F2 reruns the same script on the test split through `evals/final/` (SPEC 10.5, section 17); this protocol
  covers dev only.

## Measured-call budget

Prices are assumptions to verify at run time; the cost recorded is the usage field of each response.

- Generation: 30 questions, 24 of them with contexts (the 6 unanswerable usually retrieve none and make no
  model call), about 1,600 input and 250 output tokens each, at an assumed 3 and 15 USD per million tokens for
  the product model: about 0.22 USD. Estimate 0.22 USD, cap 0.40 USD (the "dev and eval" allowance, D18).
- Judge: 2 judges times 30 questions times 3 calls = 180 calls, about 2,000 input and 120 output tokens each,
  at an assumed 0.50 and 2.00 USD per million tokens: about 0.22 USD. Estimate 0.22 USD, cap 0.50 USD (the
  judge allowance, D18).
- Together: estimate 0.44 USD, cap 0.90 USD. Each run records `--max-cost` (checked between questions; one
  question's calls can overshoot it) and `--reason`: "product answers for the labelled sample (D126)" and
  "judge models are the thing measured against the labels (D126)". The labelling is session work on the
  subscription and is not an OpenRouter call.
