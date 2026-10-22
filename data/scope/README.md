# data/scope: scope set, lexicon and probes for the input gate (M5G)

Written under `evals/protocols/m5g-gate.md` (SPEC D103 to D118, D126, D145, D150). Owned by M5G.
Everything here is fictional: no real company, person, product or URL, no demo-company names.

## Files

| File | What it is |
|---|---|
| `scope_set.jsonl` | 420 owner messages, one JSON object per line: `sid`, `text`, `label` (`in_scope` or `out_of_scope`), `style`, `recent_turns` (zero to two prior turns, each `{role, text}` with role `owner` or `assistant`), `split` (`dev` or `test`). No `session_title` (D145). Rows s001 to s360 are the audited first set (reworded where the audit said `fix`, same sids); s361 to s420 were added in fix cycle 1. |
| `lexicon.yaml` | Stage-1 term lists (D106), `version: scope-lexicon.v4` (updated 2026-10-22): module ids and names, integration ids, the 25 protocol field ids with two to five synonyms each, knowledge-base titles, product words, trade words from the demo companies, generic business words, session and account action words, the greeting list, number-unit words, and the verb-plus-object refuse patterns for rule 4. Every rule-2 term is `strong` or `weak` (D157): the `strength` map gives it per group, and the bare or generic short forms of modules, integrations and kb titles sit in `weak_forms`. Stage 1 passes on one strong term or two weak terms; one weak term alone goes to stage 2. Every term lower-case; short forms sit next to their full form. |
| `probes.jsonl` | 80 probe rows, separate from the scope set: `sid`, `category`, `text`, `recent_turns`, `expected` (`pass`, `refuse` or `band`). The two `empty` rows carry a `note`: they never reach the gate (`validate` rejects them, SPEC section 8), so `expected` records that rejection, not a gate verdict. |
| `tools/stage1_sim.py` | Stage-1 simulation to the amended rule table (rules 1 to 4 and 6, D157): prints greeting, pass, refuse and undecided per split and label, and with `--rows` every out-of-scope pass and in-scope refusal. Pure Python plus PyYAML, no network; for the auditor and M5G. |
| `tools/check_scope.py` | The data checks listed below; exits non-zero on a failure. |
| `scope-audit.md` | Written by the auditing session agent on `agent/audit/m5g`; not edited here. |

## Counts achieved (the protocol table gives minimums)

- Labels and splits: in-scope 284 (142 dev, 142 test), out-of-scope 136 (68 dev, 68 test). Split alternates
  dev, test within each (label, style) group in file order, so every style is split evenly.
- In-scope styles: short 50, long 30, typo 46, fragment 36, multi_part 36, follow_up 36, greeting_business 22,
  session_action 20, colloquial 8 (added style: mid-length descriptions of a process in the owner's own words,
  including six rows whose business vocabulary contains a refuse-pattern noun such as recipe, joke, homework,
  lyrics, horoscope or regex). Out-of-scope styles: chat 30, creative 16, code_homework 16, unrelated_advice 22,
  personal_to_assistant 12, rule_change 16, other_products 16, other 8.
- Fix cycle 1 additions: 44 in-scope rows that stage 1 cannot settle or that exercise the rule-2-before-rule-4
  order (six multi_part rows pair a joke or poem request with a session or account action); 16 out-of-scope
  rows, 8 per split, that collide with a bare trade word or a number-unit on purpose ("my son can't find a job").
- Non-English rows (Spanish, German, Korean): in-scope 10 dev and 8 test, out-of-scope 5 dev and 5 test
  (caps 10 and 5 per split), written as original messages, not translations. Every follow_up row has one or
  two `recent_turns`; 7 out-of-scope rows also carry in-scope context so context alone does not predict the label.
- Topic spread (in-scope rows matching a lexicon group, text plus context, lexicon v4): trade words 93,
  protocol fields 77, kb titles 59, generic words 37, product words 34, integrations 33, account actions 27,
  modules 27, session actions 15; all 6 modules and all 25 protocol fields touched.
- Probes per category: clear_in_scope 9, clear_off_topic 9, ambiguous 8, negation 6, numbers_dates 7,
  follow_up 8, injected_instruction 8, mixed 8, non_english 7, empty 2, very_short 8.
- Labelling rules: a pure greeting is `in_scope` (D104). A message mixing one in-scope question or fact with
  off-topic text is `in_scope`. A rule-change or persona request with no in-scope part is `out_of_scope`.
  Follow-ups are decidable from their `recent_turns`. Labels were fixed before any probe (D107).

## Checks run before hand-over

Counts per label, split and style against the protocol table (minimums); no duplicate text after lower-casing
and whitespace collapse (also none between probes and scope rows); no pair with word-set Jaccard above 0.6;
cross-split pairs with stop-word-free Jaccard at least 0.3 listed and reworded where one side reworded the
other; no D22 phrase in any in-scope row (text or context); no demo-company or demo-owner name; short rows
under 8 words, long rows 40 to 120 words; lexicon loads with `yaml.safe_load`, all terms lower-case, 2 to 5
synonyms per field, every rule-2 term either strong or weak and never both. The checks are
`tools/check_scope.py` (all except the cross-split stop-word listing, which was done by hand); the stage-1 numbers are `tools/stage1_sim.py` (rules 1 to 4 and 6 of the amended table,
D157), which gives, per split and label (greeting / pass / refuse / undecided):

| Split | Label | greeting | pass | refuse | undecided |
|---|---|---|---|---|---|
| dev | in_scope (142) | 2 | 84 | 0 | 56 |
| dev | out_of_scope (68) | 0 | 0 | 14 | 54 |
| test | in_scope (142) | 1 | 88 | 0 | 53 |
| test | out_of_scope (68) | 0 | 2 | 14 | 52 |

No in-scope row is refused; 56 in-scope and 54 out-of-scope dev rows reach stage 2, so the D115 thresholds
are order statistics over at least 40 rows on each side. Under D157 a single weak term no longer passes, so
the deliberate collision rows (s405 to s420, and s357) now reach stage 2; none passes on two weak terms.
Two out-of-scope test rows still pass stage 1, both by rule 3 (a number with a unit: s406 "3 people", s414
"10 minutes"), within the protocol's limit of 3 of 68. Probes repeat "yes" and "ok go on" on purpose: once with
context (expected `pass`) and once without (`band`).

## Generation and audit status

Generated by a session agent (Claude, Fable) on 2026-10-22 from the catalogue, the discovery protocol, the kb
titles and headings, and the dev demo conversations (A-1, B-1, E-1; the test three not read, D149). No model
API call and no network call (D126, HARNESS rule 14). Audit status: first audit done (46 `fix`, 0 `remove`);
fix cycle 1 applied; second audit done (4 `fix`, 0 `remove`); fix cycle 2 applied (the four
rows s091, s366, s384, s387 reworded in place, every count unchanged; lexicon v3 under D157); fix cycle 3 applied (s387 given its own context, lexicon v4 and D158 counting: 19 generic kb short forms weak, `short_forms` on four protocol fields, approximate numbers in rule 3); **fourth audit pending**.
