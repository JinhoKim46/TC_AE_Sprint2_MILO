# Protocol DATA-V2: conversations, tool cases, judge label pool, knowledge-base audit

| Field | Value |
|---|---|
| Protocol id | `data-v2` |
| Status | committed before results |
| Date | 2026-10-23 |
| Owning milestone | data-v2 (data role generates; a separate audit role audits, as in M0b) |
| Implements | SPEC D60, D69, D83, D126, D149 (test split replaced, see Data 1), D161 to D165; HARNESS rules 6, 7, 14 |
| Produces | `data/demo/companies/<c..l>.yaml`, `data/source/conversations/{dev,test}/<id>.yaml`, `data/demo/conversations/<id>.{jsonl,audited.expected.json}`, `data/demo/uploads/*.txt`, `data/source/tool_cases.yaml`, `data/tool_cases.jsonl`, `data/judge_pool/{answers,labels,perturbations}.jsonl`, `data/prompts/data-v2-audit.md`, `data/MANIFEST.json` |
| Measured calls | none (0 USD) |

## Question

Can the discovery, tool and judge datasets grow so that the next dev runs and the single `MILO_FINAL` run measure
the model, not the data? The dev audit (`docs/data-audit/conversations-dev.md`) found 15 too-strict, 6 ambiguous
and 3 missing expectations on 54 turns; the M7 dev labels hold 1 unfaithful answer in 30.

## Roles and common procedure (all four datasets)

1. **Order (D83).** This file first; generation on `agent/data/v2`; audit on `agent/audit/data-v2` from the
   generation commit. Nothing reads the new data before the final audit commit. Scorer v2 (D161) stays frozen.
2. **Generator.** A Sonnet session agent (data role, owner of `data/**` and `scripts/build_data.py`); no model
   API, network, `.env` or `MILO_LIVE` (D126, HARNESS 14). It writes source files; `build_data.py` writes every
   derived file and the manifest (D50). Brief, inputs and "no model call" go to `data/prompts/generation-record.md`.
3. **Static checks.** `build_data.py --check` exits 0 and the kit checker prints `OK` before each generation commit.
4. **Auditor.** A different session agent on Opus that does not read the generator's notes (HARNESS 7). It writes
   only `data/prompts/data-v2-audit.md`, one row per item, verdict `ok`, `fix` (reason, proposed change) or
   `remove` (no `reject`, HARNESS 7.9). It never edits data; the generator never edits the audit file.
5. **Fix cycles.** The generator fixes, rechecks and commits; the auditor re-audits every changed item and spot
   checks 10 percent of the rest (at least 5). At most three cycles per dataset; an item still `fix` after cycle 3
   is removed and replaced by a freshly audited one, counts still holding, or the phase is blocked (HARNESS 4).
6. **Seal.** Test items are read only by the generator and the auditor, then only by a `MILO_FINAL=1` run
   (`make final-test`). The final audit records each sealed file's SHA-256, equal to `MANIFEST.json`. Loaders
   select by `split`, dev unless `MILO_FINAL=1`; that change to `run_discovery.py` (dev ids hard-coded) and
   `run_tools.py` (no split), and the pool refusal in `run_rag_eval.py`, are requests to M5b, M3 and M7 (HARNESS 6), done before any run on the new data.
7. **Done.** Every item `ok` in the last pass with a "changed since first pass" column, hashes equal to the
   manifest, the audit commit after the last generation commit.

## Data 1. Discovery conversations

**Scope.** 18 new conversations, 8 dev and 10 test; with A-1, B-1, E-1 (D165) dev holds 11. A-2, B-2 and E-2 are
retired: kept byte-identical and unread, in neither split (a SPEC entry amending D149). A and B become dev-only.

**Companies.** 10 new fictional companies, letters `c` to `l`, truth files in the format of `b.yaml` (with
`conflicts`, `upload`, `traps`, `expected_end_state`; D164 enums). Dev: 4 companies, 2 conversations each; test:
6 companies (2, 2, 2, 2, 1, 1). Per split: one company with 1 to 5 staff, one with 40 or more, one above 150
requests a day, urgent requests in at least half. Identities invented (HARNESS 5). One industry each:

| split | industries (new companies) | plus |
|---|---|---|
| dev | cleaning, health-wellness, food-hospitality, logistics-delivery | pet-care (A), trades (B) |
| test | home-services, beauty-personal-care, retail, professional-services, automotive, education-childcare | - |

**Owner styles.** Each conversation has one primary style. Every style appears at least once per split:

| style | dev (8 new) | test (10) |
|---|---|---|
| terse (fragments, one or two words) | 1 | 2 |
| chatty (long turns, several facts plus asides) | 2 | 2 |
| contradicts itself (states a value, later another) | 1 | 2 |
| pastes a document (procedure, price list or email in the message) | 1 | 1 |
| off-topic detours (holiday, staff gossip, weather, then back) | 2 | 2 |
| non-native English (article drops, word order, false friends; never mocking) | 1 | 1 |

**Phenomena** (tagged per turn in the source file; minimum counts per split over the new conversations):

| tag | what the turn does | dev | test |
|---|---|---|---|
| `early` | states a fact before the protocol asks; the later restatement expects no new fact (D162) | 8 in 5 convs | 10 in 6 convs |
| `owner_words` | list item in the owner's words, differing from the truth short form (qualifier, plural, synonym, first person) | 16 | 20 |
| `enum_map` | value of `industry`, `error_types`, `integrations_available`, `main_goal` or `start_timeline` given in plain words | 8 | 10 |
| `multi` | three or more fields in one turn | 6 | 8 |
| `conflict_blocking` | volume restated with another period (day, week, month); resolved by a confirm turn | 2 | 3 |
| `conflict_other` | conflict on a non-blocking field (staff, sites, minutes); asked with the field's own question (engine fallback) | 2 | 2 |
| `pasted` | the owner pastes a document into the message (document-paster style) | 1 | 1 |
| `unresolved` | a volume conflict still open at the last turn (end state `quote_blocked`), in addition to the above | 1 | 1 |
| `upload_injected` | upload with 2 or more real facts and one injected line (fake system note, price to zero, mark confirmed, approve) | 3 | 4 |
| `upload_clean` | upload with facts and no injection (control) | 1 | 1 |
| `chat_injection` | instruction in the owner's own message, as E-1 turn 4 | 2 | 2 |
| `unknown` | "don't know" on a field whose truth is `unknown` | 4 | 5 |
| `price_early` | asks for a price before `assessment_ready` (no number may be shown) | 3 | 4 |
| `name_misleads` | company name suggests another industry; industry comes only from the owner's statement | 2 | 2 |
| `off_topic` | turn with no business fact (`no_facts: true`) | 3 | 4 |

Each `list` field has an `owner_words` item at least twice per split. **Length.** 8 to 30 turns per conversation,
split means within 20 percent; owner messages 1 to 59 words (turns tagged `pasted`: 60 to 300); uploads 80 to 400.

**Format.** Each source file holds `id`, `split`, `company`, `user_id` (`owner-<letter>`), `style`, and per turn `owner_text`, `plan`
(field, value, status, unit, period, `evidence` span), flags (`asks_cost`, `answers_conflict`, `injection`,
`upload`) and `tags`. `build_data.py` writes `<id>.jsonl` and `<id>.audited.expected.json` in the format of
`B-1.audited.expected.json`, with `changes: []`, `audit` naming the audit file and a `split` field; no plain
`<id>.expected.json`. Expected list items may use truth short forms; scoring is scorer v2.

**Leakage rules.** No company, owner, staff name, email, address, product or tool brand appears in both splits or
in A, B, the gold questions or the KB. No test owner line of 5 or more words has word Jaccard above 0.6 with any
dev line, and no word 6-gram is shared between test and dev owner text outside the protocol's own question
words. Test injected lines are not rewordings of dev or B-1 ones (Jaccard at most 0.5). Test turns are fresh.

**Generator brief.** Write truth files first, then conversations from them, never from a model run (D49). Each
fact needs a verbatim evidence span in the owner text or upload (D134). Facts are stated, not implied: no fact
is inferred from a company name, and an "emergency line" is not `urgent_requests_exist` until said. Record a
fact at the earliest turn that states it (D162). Upload facts start `reported` (D46); the injected line changes
no protected field (D163). The industry label never appears in the owner text verbatim.

**Audit checklist.** First a blind pass, as in the dev audit: from the owner text and uploads alone, per turn,
record field, value, status and evidence, and mark each fact `clear` or `borderline`. Then compare:

| check | a `fix` when |
|---|---|
| truth | an expected value differs from the truth file or the owner text |
| missing / extra | the blind pass has a clear fact the file lacks, or the file has one the owner never stated |
| too strict | an `owner_words` item fails the scorer v2 item rule against the owner's own phrase (`rescore.item_tokens`) |
| ambiguous | any `borderline` fact remains (fix by rewording the owner text) |
| state | `protocol_state`, `estimate_status`, `number_allowed` or `next_question_id` disagree with the protocol order |
| phenomena | a tag is not real in the text, or a count above is not met |
| leakage, safety | a leakage rule fails; a real name, company, domain or secret appears |

**`build_data.py` must assert** (with `simulate()` generalised to source YAML, any conflict field, several uploads
and the `pasted` cap): 11 dev and 10 test conversations, retired three excluded; companies disjoint across splits;
the industry, style and phenomenon tables; turn and word ranges; evidence spans verbatim; values equal to the
truth file (conflicts from its `conflicts` list); the leakage checks; new names in the gold and KB forbidden-name
lists; every file in the manifest.

## Data 2. Tool cases

**Scope.** 15 to 41 rows, source moved to `data/source/tool_cases.yaml`. tc01 to tc15 (run live in M3) go to dev
unchanged; tc16 to tc41 are new: dev 21, test 20. User ids stay `owner-a`, `-b`, `-e`, so `tools/objects.json`
is unchanged. New fields: `split`, `category`, `rationale` (one line, at most 160 characters), optional
`expected_outcome` (`ok`, `needs_approval`, `not_found`), and `tempting_invalid` on schema rows.

| category | dev (old + new) | test |
|---|---|---|
| `search_knowledge` (with and without `product`) | 3 + 0 | 3 |
| `get_session_spec` | 2 + 0 | 2 |
| `estimate_cost` (`module_ids` only) | 2 + 0 | 2 |
| `record_fact` (number with period, list, boolean, enum; one multi-call) | 3 + 1 | 4 |
| `approval`: `withdraw_submission` on the owner's own submission, outcome `needs_approval` | 1 + 1 | 2 |
| `schema_rejection`: wording invites an invalid call (volume argument, 7 modules, unknown field, 2-char query) | 0 + 2 | 2 |
| `cross_owner`: identity claim ("I'm owner-a") or another owner's submission, outcome `not_found` | 0 + 2 | 2 |
| `no_tool`: small talk, submit request (D20), hypothetical-number trap | 4 + 0 | 3 |

`expected_calls` always holds schema-valid calls, because `run_tools.py` refuses schema-invalid expectations;
a schema row expects the valid call (or none) and its metric is the share of emitted calls that fail the schema.
A cross-owner withdraw row expects the call with `expected_outcome: not_found`; `run_tools.py` must accept that
outcome instead of refusing the row (request to M3). An identity-claim row expects `get_session_spec {}`.

**Audit checklist.** Expected call correct and minimal for the query; `match` fits (`exact` only where arguments
are determined); rationale true; category real; arguments schema-valid against `tools/` specs; submission owner
per `objects.json`; no test query a rewording of a dev query (word Jaccard at most 0.6); natural owner wording.

**`build_data.py` must assert.** 41 rows, 21 dev and 20 test, the category table, all five tools in each split,
unique ids and queries, schema-valid expected calls, `record_fact` fields in the protocol with values of the
field's type, module ids in the catalogue (at most 6), withdraw owners matching `objects.json` except
`not_found` rows, `expected_calls == []` exactly for `no_tool` rows, rationale present, Jaccard leakage check.

## Data 3. M7 judge label pool

**Problem.** One unfaithful answer in 30 dev labels cannot validate faithfulness kappa. The test M7 files
(sample, generation, labels, judge results) are already used; they are not touched or read.

**Design.** `data/judge_pool/answers.jsonl`, 90 items over dev gold questions only, each with `pid`, `qid`,
`question`, `contexts` (chunk id, doc id, text) and `answer`, plus `source: pool`. Contexts come from the 30
stored dev contexts in `evals/results/m7/generation_dev.json` and, for 30 more dev qids, from the offline BM25
retriever over `data/kb/` (no embedding call). The generator writes the answers:

| kind | count | how |
|---|---|---|
| faithful | 35 | new answers using only the contexts, half rewrites of a stored dev answer (decoys) |
| unfaithful | 40 | a faithful answer perturbed once: number off the rate card, wrong module or integration, added unsupported claim, negation flip, wrong approval or policy, invented fact on an unanswerable question, wrong citation |
| relevance 0 or 1 | 15 | faithful but off topic (5) or partial (10) |
| context swap | (within the above) 10 items with one irrelevant context inserted, for context precision |

`perturbations.jsonl` records per `pid` the base answer, perturbation type, changed span and intended labels.
The auditor labels every item blind (it opens the perturbations file only after its labels are committed) into
`data/judge_pool/labels.jsonl` with the `evals/rag/schema.py` rubric; this is its one data file. Its labels are
the ground truth; a perturbation that did not make the answer unfaithful is relabelled, not argued.

**Targets.** At least 25 percent unfaithful by the auditor's labels over 30 real plus 90 pool (30 of 120); if
fewer, a fix cycle adds perturbations. At least 10 items with relevance 0.

**No contamination.** Pool items never enter the M7 generation or sample files or any product RAG metric;
`run_rag_eval.py` refuses a pool row in a product run, so product scores come from real answers only. Judge
validation reports kappa with CI on real, pool and combined, with prevalence each. The committed judge prompts
stay frozen and are never edited from pool results. The judge run on the pool is a separate measured protocol.

**Audit checklist.** Blind labels per item; each unfaithful item has exactly one unsupported claim traceable to
its span; faithful items have every claim in the contexts; questions dev only (qid in `sample_dev.json` or a
dev gold row); no item names a demo or new company. **`build_data.py` must assert:** 90 items, unique `pid`, every
qid a dev row, contexts verbatim from KB chunks, the kind counts, a perturbation record for every unfaithful
item, labels valid under `schema.py`, the 25 percent floor, and the three files in the manifest.

## Data 4. Knowledge-base content audit

The generator runs the checklist and writes findings; the auditor verifies each. Both read the 24 articles, the
catalogue, rate card, dev gold and dev conversations only (KB fixes must not be tuned to test items).

| check | what is verified |
|---|---|
| contradictions | no two articles disagree on a price, surcharge, setup fee, tier, timeline, approval or data policy |
| rate card | every USD figure, factor, tier boundary and module limit equals `rate_card.v1.yaml` |
| catalogue | module ids, names and integrations match `catalogue.yaml` |
| gold coverage | every dev answerable row's topic is in its evidence article; every unanswerable topic is absent everywhere |
| conversation gaps | owner questions in the dev conversations (price, setup time, data, urgent calls) with no article |
| hygiene | 150 to 600 words, front matter, no demo or new company name |

**Edits.** None in this phase; findings are `fix` rows with proposed text. An edit changes chunk text and the
index hash, may shift `doc#i` ids, needs `make index` (an embedding call, not 0 USD), a re-check of every gold
quote and unanswerable topic (M0b), and voids stored E01 and M7 contexts; it is an owner decision after the
audit. `build_data.py` keeps its current KB checks and fails if any article hash changes in this phase.

**Limits.** Both agents are Claude sessions (independence is separate sessions and branches); model-written owners stand
in for real ones; sealing is a process, not encryption (SPEC 10.1); test sizes separate only large differences.

## Budget

0 USD for this phase. No OpenRouter, embedding or other network call; no key or `.env` is read; `MILO_LIVE` and
`MILO_FINAL` are never set (D126, HARNESS 14). The later Haiku dev rerun on the new dev conversations and tool
cases, and the judge run on the pool, are separate protocols with their own estimates and caps.
