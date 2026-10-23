# MILO

**Meet. Identify. Learn. Optimize.**

Every business has a story. MILO listens first.

A domain chatbot for the Sprint 2 project. An owner signs in, talks to an AI assistant about their business, and sees the evidence behind every fact it records. On submit, MILO stores a specification revision and gives the owner a research-style report: current pipeline, automation candidates, recommended modules, an indicative cost range and next steps.

## Status

Built (2026-10-22). Every milestone from S0 to M7 is merged after an independent verifier, and the security lane (SR) is done: threat model, 50 attack cases, a pen test with no open critical or high finding (`docs/security_report.md`). The single sealed test-split run (F2) is done; `docs/final_report.md` puts dev and test side by side, misses included. OpenRouter spend for the whole build: about 2.09 USD.

Not ready for production until the owner closes the checklist in `SPEC.md` section 18 (consent text, operator, role passwords and app connection strings, Supabase Auth settings, operator MFA, the access-token residual). The app refuses to start in production while those are open.

Where to look: `SPEC.md` (specification, decisions D1 to D169), `HARNESS.md` (how the build ran), `docs/integrator-decisions.md` (every build decision with its reason), `docs/owner/CHECKPOINTS_PENDING.md` (one block per milestone, with owner questions), `docs/verify/` (verifier verdicts), `docs/live-batch-ledger.md` (every paid run), `docs/m*_report.md` (milestone reports), `docs/reflection.md` (limits and lessons).

Run it locally: `uv sync`, then `make check` (offline tests and lint), `make eval` (replays every stored evaluation), and `uv run flask --app milo.web.dev:create_dev_app run` for the web app on the in-memory store.

The contract kit (`contract/` and `scripts/check_project_data.py`) is not in this repository. With the kit beside the repo, `make kit-sync` copies it in (or `make kit-sync KIT_SRC=<path>`); then the `make kit-*` targets and the kit-dependent tests run. Without it, `make check` skips those test modules and says so.

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

## License

MIT. See [LICENSE](LICENSE).
