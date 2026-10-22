# M1 report: chunking, ingest, vector store, dense retrieval, retrieval eval runner

Status: built and checked offline. No model call, network call or key use happened in this milestone (SPEC D126,
HARNESS rule 14). **The live index build and the dense dev evaluation are pending live run**; the integrator runs
them in one batch under `evals/protocols/e01-rerun.md`.

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

- Live dense baseline on the 60 dev rows (recall@5, nDCG@5, MRR, P@5, AP per type with bootstrap CI, weakest type,
  `min_score` calibration, abstention rate): **pending live run**. No number from this milestone is a MILO result yet.
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

- Cost of the live run is not recorded by the result file: `cloud.py` drops the embedding usage field (see
  `docs/requests/m1.md`).
- Embedding calls make one attempt per batch; the single retry site `llm/retry.py` serves chat only.
