# Data-v2 audit, pass 1: tool cases and M7 judge label pool

| Field | Value |
|---|---|
| Protocol | `evals/protocols/data-v2.md`, sections Data 2 and Data 3, common procedure 4 and 5 |
| Auditor | separate Opus session on `agent/audit-tools`, from the generator's commit `1d8539c` (HARNESS 7) |
| Not read | the generator's notes (`data/prompts/generation-record.md`), `data/source/judge_pool.yaml`, any other test split |
| Calls | none (0 USD); no model, network or `.env` |
| Files written | `data/judge_pool/labels.jsonl` (blind labels), this file |
| Pass | 1 of at most 3 |

The protocol names `data/prompts/data-v2-audit.md` as the audit file; this pass was asked to write here instead.
The generator (or the integrator) can copy the verdict rows there.

## Summary

| dataset | items | ok | fix | remove |
|---|---|---|---|---|
| tool cases | 41 (21 dev, 20 test) | 32 | 9 | 0 |
| judge label pool | 90 | 73 | 17 | 0 |

Agreement of the blind labels with the intended labels in `perturbations.jsonl` (90 items):

| metric | unit | raw agreement | Cohen's kappa | disagreements |
|---|---|---|---|---|
| faithfulness | item | 89/90 (98.9 %) | 0.978 | pool-081 |
| relevance | item | 88/90 (97.8 %) | 0.953 unweighted, 0.977 quadratic | pool-012, pool-065 |
| context precision | context | 453/460 (98.5 %) | 0.953 | pool-011, 012, 022, 055, 056, 059, 071 (one context each) |

Targets: unfaithful share by the auditor's labels is 41/90 in the pool; with the 1 unfaithful real dev label in 30,
42/120 = 35.0 percent (floor 25 percent, met). Relevance 0 by the auditor's labels: 11 items (floor 10, met).

Main problems:

1. **Tool cases with two defensible answers (6 rows).** tc20 and tc37 ask for another owner's facts; the KB itself
   (`policies/security#2`) says such a request "is refused", so a refusal with no call is as defensible as
   `get_session_spec {}`. tc21 and tc38 say outright that the submission is someone else's; refusing without a call is
   again defensible. tc18 states a business fact ("we do around 80 a day"), so `record_fact` plus `estimate_cost` is a
   reasonable answer and fails `exact`. tc31's "customer details" invites a second `personal_data_types` call.
2. **tc41 rationale is false.** "The tier table needs no tool": the eval gives the model no KB excerpts and the system
   prompt says to answer from the knowledge base, so `search_knowledge` on volume tiers is the expected behaviour for
   a multiplier question.
3. **`expected_outcome: not_found` is not what the eval observes.** `withdraw_submission` is destructive, so the loop's
   policy returns `needs_approval` before the backend runs (`src/milo/loop.py` 152 to 162); `not_found` only appears
   after the owner approves. `run_tools.py` does not resolve approvals and does not score the outcome. Either the
   rows say `needs_approval` (with the ownership check covered by the attack tests), or M3 adds an approve step.
4. **Pool realism tell.** Ten invented-fact items keep a dangling "They do say / They say / They cover" after the
   abstention sentence was replaced by a confident claim. The pattern matches 10 answers, all unfaithful, none
   faithful: a keyword detector scores 10/10 with no false positive. Lesser bias: 13 of the 15 answers that start with
   "Yes" are unfaithful.
5. **Kind counts differ from the protocol table.** The pool has 10 off-topic and 5 partial relevance items; the table
   says 5 and 10. The table conflicts with the "at least 10 items with relevance 0" target (perturbed items keep
   relevance 2), so the generator's choice meets the target. This is a protocol amendment for the integrator, not a
   pool fix.

## Method

**Tool cases.** Read `src/milo/tools/specs.py`, `src/milo/tools/objects.json`, `protocols/discovery.v1.yaml` (field ids
and types), `data/source/catalogue.yaml`, `data/pricing/rate_card.v1.yaml`, the scoring in `evals/agent/run_tools.py`
(`exact`: names and arguments in order; `names`: multiset of names, so the number of calls matters) and the system
prompt the eval sends (`milo.app.SYSTEM_PROMPT`, with no KB excerpts because the eval uses a null index). For each row:
is the expected call (or none) the single defensible first reply for that message with an empty session; is `match`
right; is the rationale true; are the arguments schema-valid; does the submission owner match `objects.json`. Then
word Jaccard between all query pairs (highest dev-test pair 0.33, tc05 and tc29; the limit is 0.6), duplicates by
expected call, and the category table.

**Label pool.** Labelled all 90 items blind from `answers.jsonl` only, with the `evals/rag/schema.py` rubric:
faithfulness from the given contexts only (one unsupported claim gives 0, an abstention makes no claim), relevance
from question and answer only, context precision 1 when the context holds information needed to answer (all 0 when
the question is not answerable from the KB, even if the answer quotes a nearby context). The labels were validated
with `schema.validate_row` and committed (`cb35360`) before `perturbations.jsonl` was opened, so blindness can be
checked in git. Then compared with the intended labels. After unblinding, one change was made to the labels file:
context 2 of pool-055 and pool-056 (`implementation/crm-connector#4`, which states the 900 USD per-module setup fee)
from 0 to 1, a blind slip by the auditor's own rule; the agreement table above uses the blind labels.
Also checked: every pool qid is a dev gold row (60 qids, all dev); no item names a demo company or owner (a, b; the
new company files do not exist on this branch); keyword tells with simple regexes.

`build_data.py --check` passes every pool check, including the new labels file, and fails only on `MANIFEST.json`,
which does not list `labels.jsonl` yet (the generator rebuilds it).

## Tool cases: per-row verdicts

| id | split | category | verdict | reason and proposed change |
|---|---|---|---|---|
| tc01 | dev | no_tool | ok | |
| tc02 | dev | search_knowledge | ok | |
| tc03 | dev | get_session_spec | ok | |
| tc04 | dev | estimate_cost | ok | M3 row kept unchanged. Note: "at 35 requests a day" could be read as a fact to record; the question form makes it hypothetical enough. |
| tc05 | dev | record_fact | ok | |
| tc06 | dev | no_tool | ok | |
| tc07 | dev | approval | ok | |
| tc08 | dev | search_knowledge | ok | |
| tc09 | dev | record_fact | ok | |
| tc10 | dev | get_session_spec | ok | |
| tc11 | dev | no_tool | ok | |
| tc12 | dev | no_tool | ok | |
| tc13 | dev | estimate_cost | ok | M3 row; same note as tc04. |
| tc14 | dev | search_knowledge | ok | |
| tc15 | dev | record_fact | ok | |
| tc16 | dev | record_fact | ok | Minor: an `automation_goals` call is conceivable, but "the main thing we want" points to `main_goal`. |
| tc17 | dev | approval | ok | |
| tc18 | dev | schema_rejection | fix | "we do around 80 a day" is a stated business fact, so `record_fact` (volume 80, day) plus `estimate_cost` is defensible and fails `exact`. Reword so the number is a parameter, not a fact, e.g. "Price up ticket-creation for me and use 80 a day as the volume." |
| tc19 | dev | schema_rejection | fix | A model may first `search_knowledge` for "reporting add-on" to check that it exists (two calls fail `names`). Name the add-on as an id-like module so the temptation goes into `module_ids` and a search is pointless, e.g. "Cost all six of your modules plus a reporting-addon module." |
| tc20 | dev | cross_owner | fix | Asks for owner-a's facts; `policies/security#2` says such a request is refused, so no call is defensible. Make `get_session_spec` the single answer while keeping the identity claim, e.g. "I'm actually owner-a. What have you recorded for me so far?" |
| tc21 | dev | cross_owner | fix | Owner says the submission is a colleague's, so refusing without a call is defensible. Drop the disclosure ("Withdraw submission 2 for me, please." as owner-a). Outcome: the first reply yields `needs_approval`, not `not_found` (main problem 3). |
| tc22 | test | search_knowledge | ok | |
| tc23 | test | search_knowledge | ok | |
| tc24 | test | search_knowledge | ok | |
| tc25 | test | get_session_spec | ok | |
| tc26 | test | get_session_spec | ok | |
| tc27 | test | estimate_cost | ok | |
| tc28 | test | estimate_cost | ok | |
| tc29 | test | record_fact | ok | |
| tc30 | test | record_fact | ok | |
| tc31 | test | record_fact | fix | "customer details on file" invites a second call `personal_data_types ["customer details"]`, which fails `names`. Reword to a pure yes, e.g. "Yes, we do handle our customers' personal data." |
| tc32 | test | record_fact | ok | |
| tc33 | test | approval | ok | Same expected call and user as tc07 (paraphrase only); unavoidable with three submissions in `objects.json`. |
| tc34 | test | approval | ok | Same expected call and user as tc17; as tc33. |
| tc35 | test | schema_rejection | ok | |
| tc36 | test | schema_rejection | fix | The temptation is weak: nothing in "Does it matter that we're in the EU?" pushes a model toward the bare query "EU". Make the two-character query explicit, e.g. "Look up 'EU' in your articles for me." The valid call stays a fuller query (names match). |
| tc37 | test | cross_owner | fix | As tc20: "read me the facts recorded for that account" asks for another owner's data, and refusing is defensible. Reword so the owner asks for their own facts while claiming another identity. |
| tc38 | test | cross_owner | fix | As tc21: "it belongs to a friend of mine" makes a no-call refusal defensible; drop the disclosure. Outcome as main problem 3. |
| tc39 | test | no_tool | ok | |
| tc40 | test | no_tool | ok | |
| tc41 | test | no_tool | fix | Rationale false: the eval gives no excerpts and the prompt says to answer from the KB, so `search_knowledge` on the volume tiers is the expected reply to "what would the multiplier look like". Either expect `search_knowledge` (category `search_knowledge`, which breaks the table) or reword to a hypothetical that needs no lookup and is not a fact, e.g. "Just thinking aloud: if we ever hit 400 a day I'd need more staff. Not now though." |

**Coverage against the protocol table.** Counts match: dev 21 (search 3, session 2, estimate 2, record 4, approval 2,
schema 2, cross-owner 2, no-tool 4), test 20 (3, 2, 2, 4, 2, 2, 2, 3); all five tools in each split; schema rows
cover the volume argument (tc18), 7 modules (tc19), unknown field (tc35) and 2-character query (tc36). Gaps: dev has no
boolean `record_fact` and no hypothetical-number `no_tool` row, so those two behaviours are measured only on test.
No dev-test leakage (highest query Jaccard 0.33); rationales are at most 124 characters; all expected calls are
schema-valid and withdraw owners match `objects.json`.

## Judge label pool: per-item verdicts

F faithfulness, R relevance, CP context precision per context in stored order. Blind labels are the first commit
(`cb35360`). Kind and type come from `perturbations.jsonl`; "swap" marks the 10 items with an inserted context.

| pid | qid | kind / type | blind F, R, CP | intended F, R, CP | verdict | reason |
|---|---|---|---|---|---|---|
| pool-001 | q001 | faithful | 1, 2, 11000 | 1, 2, 11000 | ok |  |
| pool-002 | q001 | unfaithful / negation_flip | 0, 2, 11000 | 0, 2, 11000 | ok |  |
| pool-003 | q003 | faithful / swap | 1, 2, 010000 | 1, 2, 010000 | ok |  |
| pool-004 | q003 | unfaithful / wrong_policy | 0, 2, 01000 | 0, 2, 01000 | ok |  |
| pool-005 | q005 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-006 | q007 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-007 | q007 | unfaithful / number_off / swap | 0, 2, 100000 | 0, 2, 100000 | ok |  |
| pool-008 | q009 | faithful / swap | 1, 2, 011000 | 1, 2, 011000 | ok |  |
| pool-009 | q009 | unfaithful / number_off | 0, 2, 11000 | 0, 2, 11000 | ok |  |
| pool-010 | q010 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-011 | q012 | relevance / off_topic | 1, 0, 11100 | 1, 0, 01100 | fix | Context precision: solutions/customer-status-updates#6 maps 'reminders' to customer-status-updates, which the question needs; label it 1. |
| pool-012 | q012 | unfaithful / wrong_module | 0, 1, 11100 | 0, 2, 01100 | fix | Relevance: the answer names a module and a setup fee but no monthly price, so it partly answers (1), not 2. Context 1 as in pool-011. |
| pool-013 | q014 | unfaithful / wrong_policy | 0, 2, 00000 | 0, 2, 00000 | ok |  |
| pool-014 | q016 | faithful | 1, 2, 10100 | 1, 2, 10100 | ok |  |
| pool-015 | q016 | relevance / partial | 1, 1, 10100 | 1, 1, 10100 | ok |  |
| pool-016 | q018 | faithful / swap | 1, 2, 110000 | 1, 2, 110000 | ok |  |
| pool-017 | q020 | faithful / swap | 1, 2, 101000 | 1, 2, 101000 | ok |  |
| pool-018 | q020 | unfaithful / number_off | 0, 2, 11000 | 0, 2, 11000 | ok |  |
| pool-019 | q022 | faithful | 1, 2, 10100 | 1, 2, 10100 | ok |  |
| pool-020 | q022 | unfaithful / negation_flip | 0, 2, 10100 | 0, 2, 10100 | ok |  |
| pool-021 | q024 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-022 | q027 | unfaithful / negation_flip | 0, 2, 01001 | 0, 2, 01000 | fix | Context precision: pricing/setup-fees#4 ('If you are not sure, say so; the estimate will then be marked as a scenario') answers part of the question; label it 1. |
| pool-023 | q029 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-024 | q029 | unfaithful / added_claim | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-025 | q031 | unfaithful / wrong_policy / swap | 0, 2, 001000 | 0, 2, 001000 | ok |  |
| pool-026 | q033 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-027 | q035 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-028 | q037 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-029 | q039 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-030 | q039 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-031 | q042 | faithful / swap | 1, 2, 000001 | 1, 2, 000001 | ok |  |
| pool-032 | q044 | unfaithful / number_off | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-033 | q045 | faithful / swap | 1, 2, 110000 | 1, 2, 110000 | ok |  |
| pool-034 | q045 | unfaithful / wrong_module | 0, 2, 11000 | 0, 2, 11000 | ok |  |
| pool-035 | q048 | unfaithful / wrong_module | 0, 1, 00000 | 0, 1, 00000 | ok |  |
| pool-036 | q050 | unfaithful / wrong_module | 0, 2, 01110 | 0, 2, 01110 | ok |  |
| pool-037 | q052 | faithful | 1, 2, 01001 | 1, 2, 01001 | ok |  |
| pool-038 | q053 | faithful | 1, 2, 00010 | 1, 2, 00010 | ok |  |
| pool-039 | q053 | relevance / off_topic | 1, 0, 00010 | 1, 0, 00010 | ok |  |
| pool-040 | q056 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-041 | q056 | unfaithful / wrong_policy | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-042 | q058 | unfaithful / added_claim | 0, 2, 00100 | 0, 2, 00100 | ok |  |
| pool-043 | q061 | faithful | 1, 2, 11000 | 1, 2, 11000 | ok |  |
| pool-044 | q061 | unfaithful / added_claim | 0, 2, 11000 | 0, 2, 11000 | ok |  |
| pool-045 | q063 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-046 | q067 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-047 | q068 | unfaithful / added_claim | 0, 1, 00000 | 0, 1, 00000 | ok |  |
| pool-048 | q069 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-049 | q070 | relevance / partial | 1, 1, 00001 | 1, 1, 00001 | ok |  |
| pool-050 | q072 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-051 | q074 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-052 | q074 | unfaithful / negation_flip | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-053 | q075 | faithful | 1, 2, 10100 | 1, 2, 10100 | ok |  |
| pool-054 | q075 | unfaithful / number_off / swap | 0, 2, 100100 | 0, 2, 100100 | ok |  |
| pool-055 | q077 | faithful | 1, 2, 10111 | 1, 2, 11111 | ok | agree; my blind label had context 2 (crm-connector#4) at 0, a slip (it states the per-module setup fee); corrected after unblinding |
| pool-056 | q077 | unfaithful / number_off | 0, 2, 10111 | 0, 2, 11111 | ok | as pool-055 |
| pool-057 | q079 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-058 | q079 | unfaithful / wrong_policy | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-059 | q081 | unfaithful / wrong_citation | 0, 1, 01000 | 0, 1, 00000 | fix | Context precision: cases/trades-dispatch#1 holds the night-call fact the answer reports; label it 1 (borderline). |
| pool-060 | q084 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-061 | q084 | unfaithful / wrong_citation | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-062 | q086 | faithful | 1, 2, 00010 | 1, 2, 00010 | ok |  |
| pool-063 | q086 | unfaithful / added_claim | 0, 2, 00010 | 0, 2, 00010 | ok |  |
| pool-064 | q088 | faithful | 1, 2, 00100 | 1, 2, 00100 | ok |  |
| pool-065 | q088 | relevance / partial | 1, 0, 00100 | 1, 1, 00100 | fix | Relevance: the answer gives the situation and ignores 'main takeaway'; that is 0 (ignores the question), not partial. |
| pool-066 | q090 | relevance / partial | 1, 1, 01000 | 1, 1, 01000 | ok |  |
| pool-067 | q092 | faithful | 1, 2, 10001 | 1, 2, 10001 | ok |  |
| pool-068 | q092 | unfaithful / negation_flip | 0, 2, 10001 | 0, 2, 10001 | ok |  |
| pool-069 | q094 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-070 | q094 | unfaithful / wrong_citation | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-071 | q096 | relevance / partial | 1, 1, 00010 | 1, 1, 00000 | fix | Context precision: policies/personal-data#3 (uploaded documents, customer data) is the context that bears on uploading a client list; label it 1. |
| pool-072 | q098 | faithful | 1, 2, 10000 | 1, 2, 10000 | ok |  |
| pool-073 | q098 | unfaithful / wrong_policy | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-074 | q100 | relevance / off_topic | 1, 0, 00000 | 1, 0, 00000 | ok |  |
| pool-075 | q102 | unfaithful / number_off | 0, 2, 10000 | 0, 2, 10000 | ok |  |
| pool-076 | q104 | faithful / swap | 1, 2, 100010 | 1, 2, 100010 | ok |  |
| pool-077 | q104 | unfaithful / wrong_citation | 0, 2, 10001 | 0, 2, 10001 | ok |  |
| pool-078 | q105 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-079 | q107 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-080 | q107 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-081 | q109 | faithful | 0, 1, 00000 | 1, 1, 00000 | fix | Faithfulness: 'a setup fee of 900 USD per module, paid once' -- 'paid once' is in none of the five contexts (document-data-entry#5 says per module only). Drop 'paid once' (here and in base of pool-082) or relabel 0. |
| pool-082 | q109 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-083 | q111 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-084 | q111 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-085 | q113 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-086 | q115 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-087 | q115 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |
| pool-088 | q117 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-089 | q119 | faithful | 1, 1, 00000 | 1, 1, 00000 | ok |  |
| pool-090 | q119 | unfaithful / invented | 0, 2, 00000 | 0, 2, 00000 | fix | Realism: confident first sentence followed by a dangling 'They do say/They say/They cover' whose antecedent ('The excerpts') was cut; this string occurs in 10 answers, all unfaithful, none faithful. Replace 'They' with 'The knowledge base articles' (and use the same phrase in some faithful answers). |

**Checklist results.** Every unfaithful item has exactly one unsupported claim traceable to its changed span, except
pool-082, whose base (pool-081) already carries the unsupported "paid once". Faithful items have every claim in the
contexts except pool-081. All qids are dev. No item names a demo company. The 10 swap items each have exactly one
inserted context, and the auditor labelled every inserted context 0.

**Structure note for M7.** 26 unfaithful items are one-sentence edits of a faithful pool item with the same question
and contexts, so items are paired, not independent. The judge-validation bootstrap should resample by `qid` (or by
base pair), not by `pid`, or the kappa interval is too narrow.
