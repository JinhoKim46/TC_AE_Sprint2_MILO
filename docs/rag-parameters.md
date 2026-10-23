# RAG parameters

Every retrieval setting MILO uses, with its value, where it is set, why, and the evidence. Updated 2026-10-23
after the KB edit round (`evals/protocols/kb-edit-v1.md`, D168).

## Settings

| Parameter | Value | Set in | Why | Evidence | Decision |
|---|---|---|---|---|---|
| Knowledge base | 24 Markdown articles, front matter as metadata (`title`, `section`, `product`, `updated`) | `data/kb/`, `rag/ingest.py` | One article per topic, 287 to 493 words | `docs/data-audit/kb-v2.md` | SPEC 4 |
| Chunker | Heading-aware, paragraph-packed; never crosses a heading; a paragraph that fits is never cut | `rag/chunk.py` | A gold quote inside one paragraph lands whole in one chunk | `tests/test_chunk.py` | D9 |
| Chunk size / overlap | 800 / 100 characters | `rag/chunk.py` `SETTINGS[0]` | Every section is under 800, so one section is one chunk | E01 compared 400/50 and 1500/150 | D9 |
| Chunk count | 167 (longest 716 characters) | | | `retrieval_dev.json` `chunk_count` | |
| Title prefix | off | `rag/chunk.py` `TITLE_PREFIX` | Measured: no gain | accepted nDCG@5 -0.007, CI [-0.068, 0.055] | D168 |
| Embedding model | `google/gemini-embedding-2`, 3072 dimensions | `rag/embed.py` | Best of five on dev | `embedding_dev_v3.json` | D5 |
| Query prefix | none | | Same for all candidates in E01 | `e01-rerun.md` | D5 |
| Vector store / similarity | Chroma, cosine | `rag/vectors.py` | Embedded, no server | | D6 |
| Top k | 5 | `rag/retrieve.py` `TOP_K` | | | D7 |
| Abstain threshold | `min_score` 0.30 | `settings.py` `MIN_SCORE` | Dev scores do not separate answerable from unanswerable, so retrieval alone cannot abstain | see below | D160 |
| Metadata filters | `section`, `product` (self-query) | `rag/selfquery.py` | | filtered nDCG@5 0.807 on 22 rows | D151 |
| Hybrid (tested, not adopted) | BM25 + dense, RRF k = 60 | `rag/fusion.py`, `rag/bm25.py` | Paired CI not above zero | M2: -0.27 nDCG@5 | D8, D24 |
| Rerank (tested, not adopted) | model reorders the top 10 | `rag/rerank.py` | CI [-0.019, 0.167] includes zero | M2 | D12, D24 |

## Current dev numbers (2026-10-23, edited KB, 48 answerable and 12 unanswerable dev rows)

| Metric | Strict (gold chunk only) | Accepted (gold or an accepted twin) |
|---|---|---|
| recall@5 | 0.917 [0.833, 0.979] | 0.938 [0.854, 1.000] |
| nDCG@5 | 0.762 [0.661, 0.849] | 0.803 [0.713, 0.885] |
| MRR | 0.718 [0.611, 0.815] | 0.765 [0.661, 0.857] |

- Strict is comparable with M1 (nDCG@5 0.761 on the unedited KB): the four KB fixes left retrieval unchanged.
  They corrected wrong facts, not findability.
- Accepted scoring (D168) counts a hit on a passage that fully answers the question as well as the gold one does.
  Ten dev rows have such a twin (`data/gold_alt.jsonl`). The gap between the two columns is the measurement
  becoming fairer, not the retriever improving.
- Paraphrase questions are the weakest type (nDCG@5 0.750, against 0.797 for identifier questions).
- Threshold: the lowest top-1 score of an answerable question (0.638) sits below the highest of an unanswerable
  one (0.756), so no `min_score` separates them. At 0.30 every question gets context; abstention must come from
  the model and the sources check (D160).

## Where the results are

- `evals/results/retrieval/retrieval_dev.json`: the product setting (replay: `make eval-retrieval-replay`).
- `evals/results/retrieval/retrieval_dev_title.json` and `kb_edit_title.json`: the title-prefix arm and the paired
  decision.
- `docs/m1_report.md`, `docs/m2_report.md`: the original baseline and the A/B on the unedited KB.
