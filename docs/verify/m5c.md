# Verifier verdict: M5c report (agent/m5c a844995)

Verdict: PASS. Offline run, no model, network or `.env`.

## Checks
- `uv run pytest tests/test_report.py`: 15 passed. `make check`: OK. `uv run pytest tests/`: 660 passed.
- Seven kit targets (`PROJECT_APP=milo.app:build_app`): all pass. `make eval`: every stored eval replays (gate skipped, not built).
- Hard rules: `git diff main...agent/m5c` has no model client, no openrouter URL, no key pattern, no `/home/`. Owned paths only: `src/milo/report/**`, `tests/test_report.py`, `docs/m5c_report.md`.

## A-1 recomputed by hand (rate card rate-card-v1-2026-10)
Facts: 35 requests/day (tier up to 60, factor 1.25); modules by the keyword table: phone-intake 690, ticket-creation 290, customer-status-updates 240 (sum 1220); csv-export surcharge 0.
Monthly base 1220 x 1.25 = 1525.00; low x 0.90 = 1372.50; high x 1.20 = 1830.00. Setup 3 x 900 = 2700, fixed.
The report gives exactly 1372.5 / 1525.0 / 1830.0 and setup 2700.0 (lines 862.5, 362.5, 300.0, 0.0). Every `{value, source}` pointer resolved (no bad pointers).

## Behaviours
- E-1 (collecting): no numeric leaf anywhere; `numbers.allowed` false with a reason.
- Volume conflict on A-1: `quote_blocked`, no numeric leaf, the value 35 not shown; only the protocol conflict question and a quoted evidence line.
- Non-blocking conflict (employee_count with candidate values): field absent from the profile, candidate value not shown, appears as an open question.
- Range: monthly total only (`range_applies_to: monthly_total_only`, setup `fixed`, decision 31).
- Owner text: injected `<script>` text appears only under `{"quoted", "source"}`; an injected 9999 is never a numeric leaf.
- Removing any of base_monthly_usd, integration_surcharge_monthly_usd, setup_fee_usd, range, eligible_share raises `MissingRateCardKey`.

## Findings (none blocking)
1. Low: `resolve_source` returns None for any `derived.*` pointer without checking the name, so those pointers are not truly verified.
2. Low: the conflict list takes evidence through `_evidence` with no pii check; a conflicting `owner_email` would quote the address (the appendix does skip pii).
3. Info: decision log in this branch ends at 74; decisions 75 to 77 were not present, so those were not checked beyond the task text.
4. Info: the report formula uses saved minutes rather than the SPEC `t_manual - t_automated`; documented as open point 1 in `docs/m5c_report.md`.
