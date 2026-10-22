# data/scope: scope set, lexicon and probes for the input gate (M5G)

Written under `evals/protocols/m5g-gate.md` (SPEC D103 to D118, D126, D145, D150). Owned by M5G.
Everything here is fictional: no real company, person, product or URL, no demo-company names.

## Files

| File | What it is |
|---|---|
| `scope_set.jsonl` | 420 owner messages, one JSON object per line: `sid`, `text`, `label` (`in_scope` or `out_of_scope`), `style`, `recent_turns` (zero to two prior turns, each `{role, text}` with role `owner` or `assistant`), `split` (`dev` or `test`). No `session_title` (D145). Rows s001 to s360 are the audited first set (reworded where the audit said `fix`, same sids); s361 to s420 were added in fix cycle 1. |
| `lexicon.yaml` | Stage-1 term lists (D106), `version: scope-lexicon.v2`: module ids and names, integration ids, the 25 protocol field ids with two to five synonyms each, knowledge-base titles, product words, trade words from the demo companies, session and account action words (rule-2 terms as well as rule-5), the greeting list, number-unit words, and the verb-plus-object refuse patterns for rule 4. Every term lower-case; short forms sit next to their full form. |
| `probes.jsonl` | 80 probe rows, separate from the scope set: `sid`, `category`, `text`, `recent_turns`, `expected` (`pass`, `refuse` or `band`). The two `empty` rows carry a `note`: they never reach the gate (`validate` rejects them, SPEC section 8), so `expected` records that rejection, not a gate verdict. |
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
- Topic spread (in-scope rows matching a lexicon group, text plus context): trade words 98, protocol fields 77,
  kb titles 58, session and account actions 42, product words 34, integrations 33, modules 27; all 6 modules
  and all 25 protocol fields touched.
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
synonyms per field; a simulation of stage-1 rules 1 to 5 against this lexicon: no in-scope row refused;
50 in-scope and 46 out-of-scope dev rows reach stage 2 (41 and 46 on test), so the D115 thresholds are order
statistics over at least 40 rows on each side; 8 out-of-scope rows per split pass stage 1 by a bare trade word
or number-unit (the deliberate collisions), which caps the two-stage refusal rate on test at 60 of 68 unless
the lexicon is narrowed before the first probe. Probes repeat "yes" and "ok go on" on purpose: once with
context (expected `pass`) and once without (`band`).

## Generation and audit status

Generated by a session agent (Claude, Fable) on 2026-10-22 from the catalogue, the discovery protocol, the kb
titles and headings, and the dev demo conversations (A-1, B-1, E-1; the test three not read, D149). No model
API call and no network call (D126, HARNESS rule 14). Audit status: first audit done (46 `fix`, 0 `remove`);
fix cycle 1 applied; **second audit pending**.
