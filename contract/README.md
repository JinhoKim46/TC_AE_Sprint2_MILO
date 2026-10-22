# Contract tests

The interface a Sprint 2 project must expose. The tests are domain-agnostic, offline (sockets blocked) and free.
`fakes.py` holds its own scripted chat model, hash embedder and fake tools backend, so your project needs no course code.

## Run

```bash
# toy app (default): all pass
cd sprint2/project-kit/contract && uv run pytest
# your project, after copying contract/ into it
PROJECT_APP=yourpkg.app:build_app uv run pytest contract
uv run pytest contract -k tools      # M3;  -k security (M4);  -k errors (M5);  -k sources (M1/M2)
```

If `PROJECT_APP` cannot be imported, every test errors with a message. An empty project fails all nine.

## The interface

```python
build_app(chat_model, embeddings, tools_backend) -> App

App.ingest(docs: list[Document]) -> None                 # Document(doc_id, text, metadata)
App.ask(question: str, user_id: str, thread_id: str) -> Response
Response(answer, sources: list[Source], tool_results: list[ToolResult],
         cost_usd: float, stopped: bool, error: str | None)
Source(chunk_id, doc_id, score, text)
ToolResult(name, arguments, output, ok, policy_decision)   # "allowed" | "needs_approval" | "denied"
```

Collaborators the app must use (adapt your real clients to these, for example a LangChain model behind `invoke`):

- `chat_model.invoke(messages, tools=None) -> ChatReply(content, tool_calls, usage)`, with `messages` as role/content dicts
  and `chat_model.price_usd_per_mtok = (input, output)`.
- `embeddings.embed(texts) -> list[list[float]]`.
- `tools_backend.specs() -> list[ToolSpec(name, description, parameters, risk)]` with risk `read`, `write` or `destructive`;
  `tools_backend.call(name, args, *, user_id) -> Any`. Offer the specs to the model and run tools only through `call`.

## What each test requires

| Test | Requirement |
|---|---|
| `test_sources_present_for_kb_answer` | An answer built from the KB lists sources (chunk id, document, score); a question quoting a passage ranks that document first |
| `test_abstains_without_model_call_on_empty_retrieval` | Nothing retrieved: no model call, a polite answer, no sources, cost 0 |
| `test_tools_tool_result_shown_with_policy_decision` | A tool call appears in `tool_results` with name, arguments, output, `ok` and the policy decision |
| `test_security_destructive_tool_needs_approval` | A destructive tool is not executed without approval; the decision is `needs_approval` and `stopped` is true |
| `test_security_identity_not_from_arguments` | The identity passed to the backend is the `user_id` given to `ask`, never a value the model put in the arguments |
| `test_errors_invalid_input_rejected_with_typed_error` | Empty, over-length (more than 4000 characters) and control-character input: `error` starts with `invalid_input`, no model call, a user message |
| `test_errors_provider_error_becomes_user_message_not_traceback` | Provider failures and timeouts become `error` starting with `provider_error` and a plain user message; nothing raises; no traceback text |
| `test_threads_isolated` | Each `thread_id` keeps its own history; another thread never sees it |
| `test_cost_reported` | `cost_usd` equals tokens times `price_usd_per_mtok` |

Tool and security questions also quote the KB, so the retrieval is never empty for them. If your app answers tool-only
questions without retrieval, it still passes.

## The toy app

`example_app.py` is a stub that satisfies the contract (paragraph chunks, cosine retrieval, a tiny tool loop, a risk
policy). It is there to show the interface. Do not copy it as your project. `EXAMPLE_APP_MUTATION` switches on one
defect at a time (`no_policy`, `identity_from_args`, `leak_traceback`, `shared_threads`); the kit tests check that the
matching test, and only that one, fails.
