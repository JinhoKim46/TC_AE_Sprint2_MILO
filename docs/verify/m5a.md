# Verify M5a: verdict PASS

Verified commit: 639f145 (branch agent/m5a, checked out as verify/m5a). Fresh worktree, `uv sync` run. No model or network call, `.env` not read.

## Done-line (checklist 2), last lines, $HOME as ~
- `uv run pytest tests/test_llm.py tests/test_errors.py`: 45 passed (33 llm, 12 errors).
- `make kit-errors`: 2 passed, 7 deselected.
- `make kit-cost`: 1 passed, 8 deselected.
- `make check`: ruff, mypy ok; 74 passed; data checks "OK: 0 problem(s)"; size check clean; exit 0.
- `uv run pytest tests/`: 74 passed.
- `make kit-sources kit-abstains kit-tools kit-security kit-errors kit-threads kit-cost` (the seven targets SPEC 13 names): exit 0.
- `make eval-chat-replay`: `replay OK for evals/chat/fixtures/chat_dev_fixture.json: 12 rows, 2 models`.
- `make eval-chat` without MILO_LIVE: refuses, exit 2 ("needs MILO_LIVE=1").

## Hard rules (checklist 3)
- `openrouter.ai` under src, evals, tests: only `src/milo/llm/cloud.py:91` and the pre-S0 `evals/retrieval/compare_embeddings.py:61` (M0b rewrites). OK.
- `sk-or-v1`, `AKIA`, `ghp_`, `PRIVATE KEY`, `/home/` in `git diff main...HEAD`: no hits.
- `import milo.app` with socket connect and getaddrinfo disabled: succeeds; `openai` and `httpx` not loaded (client imported lazily in `cloud._make_client`).
- Retry loop: one site, `src/milo/llm/retry.py:61`. No other retry/sleep loops in src.
- Model client: `OpenAI(` only in `cloud.py`. (tests/test_s0_smoke.py mentions it only as a needle.)
- Supabase names: only `src/milo/settings.py` and `.env.example` in code; other hits are docs (SPEC, HARNESS, integrator-decisions) and S0's `tests/test_s0_smoke.py` guard test, none from M5a.
- `gold.jsonl` named only in `evals/common.py` and pre-S0 `compare_embeddings.py`; M5a's runner loads gold through `common.load_gold`, and the test split needs MILO_FINAL (D149).

## Owned paths (4)
`git diff --name-only main...agent/m5a`: Makefile, docs/m5a_report.md, evals/chat/{compare_chat.py, fixtures/chat_dev_fixture.json, prompts/extract.v1.md, prompts/reply.v1.md}, evals/results/e02/.gitkeep, src/milo/llm/{client,cloud,retry,think,tokens}.py, tests/test_errors.py, tests/test_llm.py. All inside the M5a rows. The one outside path, Makefile, adds only `eval-chat` and `eval-chat-replay` (and their .PHONY entries): the allowed E02 addition. Protocol `evals/protocols/e02-chat-compare.md` is already on main, so it precedes any result.

## Report numbers (5)
`docs/m5a_report.md` quotes no metric, cost or count. There is no `evals/results/e02/*.json` (only .gitkeep); no D56 results file is required. The fixture `evals/chat/fixtures/chat_dev_fixture.json` is labelled synthetic ("not a result"), has no D56 fields, and replay proves the scoring code only.

## Replay (6)
Matches the stored fixture rows exactly (12 rows, 2 models). Not evidence of any model result.

Items 7 to 9: not applicable (no M1 retrieval, no store/UI, no gold). Item 1: fresh worktree.

## Code review against SPEC 9 and D23
Conforms: temperature 0 and `reasoning.enabled=false` with `usage.include` (cloud.py:195-205); cost from `usage.cost`, else the section 9 formula (cloud.py:214-228); `strip_think` handles closed, unterminated and orphan blocks (think.py); at most 3 attempts, backoff 0.5 s and 1.0 s via injected sleep (retry.py:49-73); timeout detection by `TimeoutError` or class name (retry.py:36); every final failure is `provider_error:timeout|unavailable`; one JSON re-ask via `retry.py` (client.py:66-88); ledger check before each paid call (cloud.py:193-194) and a plan check against the 1.20 USD cap in the live runner (compare_chat.py:655-661).

Findings (none fails the milestone):
1. retry.py:24,46,68 and 41-46: HTTP 400/401/402/403/404/413/422 are not retried. D23a says any exception is `provider_error` and caps at 3 attempts; it does not require retrying permanent statuses. Disclosed in the report. Note only.
2. retry.py:66-73: `SpendRefused` becomes `provider_error:unavailable`. D23 has no spend code, so this is consistent but the label hides the cause; tested at tests/test_llm.py:214. Note.
3. client.py:30-50 and cloud.py:176,193: `build_chat` defaults `ledger=None`, so the product path has no reserve check unless the caller passes a ledger; `check()` uses planned 0.0, so a single call's own cost is not reserved (only the live runner plans). D18/D126 concern the eval path, so acceptable; the integrator should pass a ledger when wiring E02 live.
4. cloud.py:203: `reasoning: {enabled: false}` and `temperature` support are unverified per model (report open items); `strip_think` is the fallback. Not testable offline.
5. client.py:23 duplicates `loop.call_cost`; kept equal by a test. Note.
