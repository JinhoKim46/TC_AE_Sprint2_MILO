# MILO

**Meet. Identify. Learn. Optimize.**

Every business has a story. MILO listens first.

A domain chatbot built for the Sprint 2 project: retrieval-augmented answers, tool calls, input and policy checks, typed errors and a Flask web UI that shows the sources and tool results behind every answer.

## Status

Pre-M0. The repository holds the project notes and this README. No application code or data yet.

## Milestones

| ID | Deliverable |
|---|---|
| M0 | Domain and data: knowledge base, gold questions, tool cases, attacks |
| M1 | Retrieval baseline with recall@5 and nDCG@5 |
| M2 | Advanced RAG with an A/B against M1 |
| M3 | Domain tools with JSON schemas and risk tiers |
| M4 | Domain security: input validation, policy in code, measured attack success rate |
| M5 | LangChain integration on OpenRouter with typed errors and bounded retries |
| M6 | Flask web UI: chat, sources, tool calls, progress |
| M7 | Evaluation and reflection (bonus) |

Milestones run in order. Each one is done when its evidence is shown, not when the code exists.

## Repository notes

- `docs/` is local only and listed in `.gitignore`. It holds the project guidelines and is not published.
- Secrets never go in the repo. Use `.env.example` for variable names.
- Every model is called through OpenRouter. Development and evaluation use cheap OpenRouter models; the product uses Claude Sonnet through OpenRouter. The budget is the owner's OpenRouter balance.
