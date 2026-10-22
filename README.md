# MILO

**Meet. Identify. Learn. Optimize.**

Every business has a story. MILO listens first.

A domain chatbot for the Sprint 2 project. An owner signs in, talks to an AI assistant about their business, and sees the evidence behind every fact it records. On submit, MILO stores a specification revision and gives the owner a research-style report: current pipeline, automation candidates, recommended modules, an indicative cost range and next steps.

## Status

Specification v0.9.1 is complete. The embedding comparison is done and provisional. The build has not started: no application code exists yet. The data (knowledge base, demo companies, conversations, tool cases, attacks, protocol) is committed and passes the course checker. The gold expansion (M0b) is next, then the scaffold (S0).

Read `SPEC.md` for the specification and `HARNESS.md` for how the build runs. `docs/experiments.html` logs every experiment, and `docs/embedding-report.html` is the interactive embedding report.

## Stack

- Python 3.12, Flask, Jinja templates, vanilla JavaScript
- Supabase PostgreSQL: a test project and a production project
- OpenRouter for every model call: the product uses Claude Sonnet 5.5; development and evaluation use cheaper models chosen by measurement
- Tests run offline, with scripted fakes and an in-memory store

## Milestones

| ID | Milestone |
|---|---|
| M0 | Data: knowledge base, demo companies, conversations, tool cases, attacks, protocol |
| M0b | Gold expansion to 120 questions, with an independent audit |
| M1 | Retrieval baseline (dense) with recall@5 and nDCG@5 |
| M2 | Advanced RAG: rewrite, self-query, hybrid, with an A/B against M1 |
| M3 | Tools and pricing |
| M4 | Security: validation, policy in code, identity, measured attack success rate |
| M5a | LLM client, retries and typed errors |
| M5-store | Database, migrations and row-level security |
| M5b | Discovery: protocol, facts, chain, turns, approvals |
| M5c | Report |
| M5G | Scope gate (rules) and input gate (decision model Jev) |
| M6 | Web UI: pages, components and the recorded walkthrough |
| M7 | Evaluation and reflection |

Milestones run in the order in `SPEC.md` section 13. Each one is done when its verifier passes, and the owner's checkpoint answers are recorded as pending until the owner answers them.

## Repository notes

- `docs/` holds reports, logs and the history map. Owner-only guideline files in `docs/` are git-ignored.
- Secrets never go in the repo. Keys and connection strings live in the git-ignored `.env`. `.env.example` lists the variable names.
- Git history was rewritten once on 2026-10-22. `docs/history-map.md` maps old commit IDs to new ones.
