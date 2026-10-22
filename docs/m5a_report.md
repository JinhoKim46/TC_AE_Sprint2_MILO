# M5a report: LLM client, retries, typed errors, E02 runner

Status: built, offline checks pass. No model call, network call or key use happened in this milestone (SPEC D126).

## What exists

- `src/milo/llm/cloud.py`: the only HTTP path (OpenAI SDK with the OpenRouter base URL, `max_retries=0`). Request pins the
  model, temperature 0, `usage.include`, and `reasoning.enabled=false`. Cost is the usage field's `cost`, else the section 9
  formula with the pinned price. `fetch_catalogue` reads the models list for the E02 slug check.
- `src/milo/llm/retry.py`: the one retry site (D23a). Timeouts, 408/409/425/429, 5xx, connection errors and errors without a
  status are retried (3 attempts, 0.5 s then 1.0 s). 400/401/402/403/404/413/422 and `SpendRefused` are not retried. Every
  final failure is `provider_error:timeout` or `provider_error:unavailable`.
- `src/milo/llm/think.py`, `tokens.py`, `client.py`: think stripping (D23b), a deterministic token estimate, the cost formula,
  `build_chat`, and `invoke_json` (one re-ask through `retry.py`, not a retry).
- `evals/chat/compare_chat.py`, two frozen prompts, `evals/chat/fixtures/chat_dev_fixture.json` (synthetic, labelled), and
  `evals/results/e02/` (empty). Targets: `make eval-chat` (needs `MILO_LIVE=1`), `make eval-chat-replay`.

## Decisions

- Permanent HTTP statuses are not retried (D23a says "at most 3"): the same call cannot succeed and a retry would only spend time.
- `SpendRefused` raised inside `invoke` becomes `provider_error:unavailable` without retry, so `loop.py` keeps its no-raise rule.
- Token counting is about four characters per token; billing never uses it.
- Candidate slugs in the runner are proposals; `verify_slugs` pins only those the catalogue lists with a price.
- Test conversations load only with `MILO_FINAL=1` (D149), so protocol step 7 is left to a final run.
- Task A scoring detail: a fact for a field expected only in an earlier turn is neutral; period `month` or `week` is divided by 21 or 5.

## Open items

- Catalogue slugs, per-model temperature support and reasoning-disable support are unverified until the integrator's live run.
- The runner needs the protocol's blind-grading `grade` field added to rows by a session agent; metrics read it if present.
- `cost_usd` in `llm/client.py` duplicates `call_cost` in `loop.py` (integrator-owned, not edited); a test keeps them equal.
