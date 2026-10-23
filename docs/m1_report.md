# M1 report: chunking, ingest, vector store, dense retrieval, retrieval eval runner

Note (2026-10-23): numbers here describe the knowledge base before the D168 edit round; current dev numbers are in
`docs/rag-parameters.md`.

Status: built and checked offline; the live index build and the dense dev evaluation ran in the live batch on
2026-10-22 (`docs/live-batch-ledger.md`), after the E01c rerun confirmed the embedding model.

## What exists

- `src/milo/rag/chunk.py`: heading-aware, paragraph-packed chunker with the three settings (800/100 default, 400/50,
  1500/150). A paragraph that fits is never cut; overlap stays inside a section. Chunk id `<doc_id>#<index>`.
- `src/milo/rag/ingest.py`: reads `data/kb/**/*.md`, front matter as metadata (`title`, `section`, `product`, `updated`,
  plus `updated_num` when `updated` exists), builds an index under `data/index/<model>-<size>-<overlap>/` with `index.json`.
- `src/milo/rag/vectors.py`: Chroma store (D6), cosine space, in memory or on disk. Filters on `section`, `product`,
  `updated_after`. Search is an exact float64 cosine scan so a ranking is reproducible.
- `src/milo/rag/retrieve.py`: `Retriever.ingest` and `search` replace the S0 seam; hits below `min_score` are dropped.
- `src/milo/rag/embed.py`: `OpenRouterEmbedder` (through `milo.llm.cloud`, batches of 64) and `FakeEmbedder`.
- `evals/retrieval/run_retrieval.py`: `--dry` (fake embedder, committed fixture), `--replay`, live (needs `MILO_LIVE=1`).

## Results

- Embedding choice (E01c, `evals/results/retrieval/embedding_dev_v3.json`, protocol `e01-rerun.md`): at 800/100 on
  48 answerable dev questions `google/gemini-embedding-2` has nDCG@5 0.761; no candidate's paired interval lies above
  zero (runner-up `mistralai/mistral-embed-2312` -0.087 [-0.176, -0.004]; `openai/text-embedding-3-large` -0.089
  [-0.192, 0.009]). D5 stays: confirmed on 48 dev questions, runner-up excluded. Chunk setting stays 800/100 (D9).
- Live dense baseline, `evals/results/retrieval/retrieval_dev.json` (60 dev rows, 48 answerable, 167 chunks, 3072
  dimensions, `google/gemini-embedding-2`, 800/100; command `MILO_LIVE=1 python evals/retrieval/run_retrieval.py`):

  | Slice | n | recall@5 | nDCG@5 | MRR | P@5 | AP |
  |---|---|---|---|---|---|---|
  | all answerable | 48 | 0.917 [0.833, 0.979] | 0.761 [0.660, 0.848] | 0.716 [0.609, 0.815] | 0.183 | 0.716 |
  | identifier | 12 | 0.917 [0.750, 1.000] | 0.797 [0.610, 0.958] | 0.767 | 0.183 | 0.767 |
  | paraphrase | 36 | 0.917 [0.806, 1.000] | 0.749 [0.636, 0.853] | 0.700 | 0.183 | 0.700 |

  Weakest type: paraphrase (nDCG@5 0.749). No answerable row lacks a relevant chunk. With the gold filter (22 rows):
  nDCG@5 0.807 against 0.732 unfiltered.
- `min_score` calibration (D25): answerable top-1 minimum 0.638, unanswerable top-1 maximum 0.756 over 12 rows, so
  the two are **not separable**. The balanced-accuracy rule picks 0.73: 56 percent of answerable questions answered,
  83 percent of unanswerable ones abstained. At the current 0.30 every question is answered and none abstains. The
  value is filed as a request (`docs/requests/live-batch.md`); `settings.py` is the integrator's.
- Cost: index build about 0.002 USD and evaluation 0.00225 USD (11,248 tokens), from the usage field.
  `make eval-retrieval-replay` matches (rows recomputed; 60 stored query vectors re-ranked within 0.005).
- The dry result `evals/results/retrieval/retrieval_dry.json` comes from a synthetic fixture and the fake embedder. It
  exercises the harness only and is not evidence about retrieval quality.
- Offline check on real data: every answerable dev quote lands whole in a chunk at 800/100
  (`tests/test_chunk.py::test_every_dev_quote_lands_in_a_chunk_at_the_product_setting`), so the M0b quote rule holds.

## Decisions

- Chunk packing by paragraph, not fixed windows as in the pre-S0 script, so quotes (at most 150 characters, one heading,
  D78) are never split by the 100-character overlap. The chunk hash will differ from `embedding_dev_v2.json`.
- Chroma holds data and filters (D6); the scan is exact instead of HNSW, so replay and the 0.005 live rerun (HARNESS 7.7)
  do not depend on an approximate index.
- Primary metrics use no filters (the dense baseline, D7); the same rows also carry a filtered ranking for rows with a
  gold filter (keys `section`, `product`, D151) to test the metadata filter.
- `min_score` is calibrated from top-1 scores of dev unanswerable rows against answerable rows (D25): the lowest
  threshold at two decimals that maximises balanced accuracy. The value goes to the report after the live run; the
  integrator copies it to `settings.py` (not an M1 path).
- Query vectors are stored as float32 base64 (about 1 MB at 3072 dimensions, under the 2 MB repo limit) and rankings
  are computed from the same float32 values, so replay re-ranks exactly.
- `compare_embeddings.py` is left as it is (pre-S0, owned by the M0b rewrite under `e01-rerun.md`).

## Limits

- Cost is recorded from the embedding usage field (the `cloud.py` gap of `docs/requests/m1.md` item 1 is closed).
- Embedding calls make one attempt per batch; the single retry site `llm/retry.py` serves chat only.
