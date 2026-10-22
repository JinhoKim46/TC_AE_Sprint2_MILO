# M5c report: canonical report JSON from a stored specification

Status: built on `agent/m5c`. Offline only: no model, network or `.env` (D126).

## What was built

| Path | What it does |
|---|---|
| `src/milo/report/build.py` | `build_report(snapshot)` returns the canonical report JSON (SPEC section 7, ten sections in order). `make_snapshot` builds the `spec_snapshot_json` shape (`{protocol_version, facts: [{field_id, value, unit, period, status, evidence_text}]}`). `attach_fields` gives the `attach_report` columns (status, monthly low, base, high, currency, rate card version). `resolve_source` checks a source pointer |
| `tests/test_report.py` | 15 tests (below) |

M6 owns the renderer (D79) and escapes owner text (D139); this module only builds JSON.

## Rules enforced in code

- Every number is a figure `{value, source: [...]}`. Sources are `fact.<field_id>`, `rate_card.<key path>`,
  `protocol.conversions.<key>` or `derived.<name>`. No other number exists in the JSON. The report never reads
  model reply text, so a figure the model wrote cannot reach it (decision 66).
- When the protocol does not allow a figure (collecting, blocked, volume above the last tier, no module matched)
  the report holds no number at all, not even a profile count; `numbers.allowed` is false with a reason.
- A conflicting fact becomes an open question with the protocol's `conflict_question`; its values are not shown.
- The 0.90/1.00/1.20 range applies to the monthly total; the setup fee is one fixed figure (decision 31), read from
  the estimator's raw setup line, not from its ranged setup.
- Owner text (free-text facts and their evidence) is only `{"quoted": ..., "source": ...}`. `owner_email` (pii)
  is left out.
- A needed rate-card key that is absent raises `MissingRateCardKey` (also `SnapshotError` for a bad snapshot).
- Modules come from a keyword table (`MODULE_SIGNALS`, rule text over facts) and are scored by minutes saved per
  business day (volume x saved minutes x eligible share). Cost comes from `plan_estimate` (M3), unchanged.

## Open points for the integrator

1. SPEC section 7 writes the return formula with `t_manual - t_automated`; the rate card file defines
   `automated_minutes_per_request` as minutes one automated request saves. The report uses the file's meaning
   (saved minutes, capped at the reported manual time). Hours per month = daily volume x saved x share / 60 x
   business days per month; per-module figures may overlap, so the total carries `modules_may_overlap`.
2. The snapshot shape above is defined here; the submit path (M6) must write it. Conflicting facts may carry
   their candidate values; the report ignores them.
3. `MODULE_SIGNALS` keywords are a first rule set, tuned on the dev demo companies only.

## Tests

A-1 (indicative, monthly 1372.50 / 1525.00 / 1830.00, setup 2700.00), B-1 (scenario, confirmed volume) and E-1
(collecting, no number) from snapshots hand-built from the `.expected.json` files: every numeric leaf has a
resolvable source and, for a single fact source, equals the fact. Plus: collecting gives no figure; conflict shows
a question; range on the monthly total only (a wider card leaves the setup fee unchanged); quoted-only owner text
with an injected string; typed errors for a missing module, share or integration key; above-tier volume.
