# Data audit: expected files of the dev discovery conversations

Scope: the ground truth of the discovery evaluation (`evals/protocols/m5b-discovery.md`) for the three dev
conversations A-1, B-1 and E-1 (54 turns). Question: how much of the live extraction F1 of 0.6992
(`evals/results/discovery/discovery_live.json`) comes from the expected files and the scorer, and how much from
the model? No model call was made. The test conversations A-2, B-2, E-2 and `discovery_test.json` were not opened.

## Method

1. **Blind pass** (`docs/data-audit/blind-dev.json`). From the owner messages (`data/demo/conversations/*.jsonl`)
   and B-1's upload alone, before opening any expected file or model output, I wrote down per turn what a careful
   interviewer would record: field, value, status, unit/period and a verbatim evidence span (all spans checked as
   substrings of the turn's text, D134). Each fact is marked `clear` or `borderline` (implied or partial).
   Inputs read first: `protocols/discovery.v1.yaml`, SPEC section 5, D46, D134, the protocol doc, the scorer in
   `evals/discovery/run_discovery.py`, the truth files `data/demo/companies/{a,b}.yaml`.
2. **Compare.** Then I read the expected files and the live rows: the recorded facts (`actual.facts`, the
   `fact_recorded` events the scorer sees) and the raw extract-call output stored in `calls`. The raw output
   matters, because the merge (`src/milo/facts/merge.py`) emits no event when a value is already in the store and
   accumulates list items. Every turn and field where blind, expected and model differ is classified once.
   Differences in phrasing only between my blind wording and an expected value that the model also matches
   (for example "phone numbers" vs "pet owners' phone numbers") are not counted.
3. **Audited files.** `data/demo/conversations/{A-1,B-1,E-1}.audited.expected.json`: the original files plus a
   `changes` list, changing only `expected_missing` and `expected_wrong` items.
4. **Re-score** with `docs/data-audit/rescore.py` (offline, no `milo` import). Scenario (a) reproduces the stored
   pooled F1 exactly (0.6992), which validates the re-implementation of the scorer.

## Disagreements

Blind: `-` = not recorded, `(b)` = borderline. Model: the recorded fact; "no event" means the model extracted the
fact but the merge emitted nothing because the store already held it.

| # | turn | field | blind | expected | model (recorded) | class | why |
|---|---|---|---|---|---|---|---|
| 1 | A-1#1 | industry | - | - | `pet-grooming`, evidence "Maple & Paw Grooming" | model_wrong | inferred from the company name; the owner states the industry next turn. Causes #2's missing event |
| 2 | A-1#2 | industry | "pet grooming" | `pet-services` | no event (stored at #1 as `pet-grooming`) | ambiguous | `industry` is a free string, but the extract prompt asks for a hyphenated label and names `pet-services` as the example. The owner never says "services"; `pet-grooming` is an equally good label. Only a closed vocabulary settles it |
| 3 | A-1#8 | tools_in_use | `[spreadsheet]` (b) | - | `[spreadsheet]` | expected_missing | "type it into the spreadsheet" names the tool in use |
| 4 | A-1#9 | tools_in_use | `[shared spreadsheet, booking widget with CSV export]` | `[spreadsheet, booking widget with CSV export]` | `[spreadsheet, shared spreadsheet, booking widget]` | expected_too_strict | same tools; the scorer compares the cumulative store list as an exact set, and the model put "CSV export" into integrations_available |
| 5 | A-1#9 | integrations_available | `[csv-export]` (b) | - | `[csv-export]` | expected_missing | "a booking widget on the site that can export CSV" states the capability |
| 6 | A-1#10 | steps_in_process | owner's 5 steps | `take request, write in spreadsheet, confirm by text, reminder by hand, mark done` | `take the request, write it in the spreadsheet, confirm by text, send a reminder by hand the day before, mark it done` | expected_too_strict | the expected items are the truth-file short forms; the model uses the owner's words, as the prompt asks |
| 7 | A-1#11 | approval_steps | `owner approves refunds` | `owner approves refunds only` | `refunds` | expected_too_strict | same approval; the model's item names what is approved, the evidence names who |
| 8 | A-1#12 | pain_points | owner's 3 items | `missed calls at peak times, reminders sent by hand, no-shows` | `missing calls at peak times, reminders all sent by hand, no-shows cost us` | expected_too_strict | paraphrase |
| 9 | A-1#12 | error_types | `[missed]` (b) | - | - | ambiguous | "we miss calls" is a pain point; whether it is also an error kind is a judgement call. Blind only |
| 10 | A-1#17 | integrations_available | `[csv-export]` | `[csv-export]` | no event (stored at #9) | expected_too_strict | the model extracted it again; the scorer only sees change events |
| 11 | B-1#1 | industry | - | - | `trades-plumbing`, evidence "Ironbridge Plumbing & Heating" | model_wrong | inferred from the company name (as #1) |
| 12 | B-1#2 | industry | "plumbing and heating trade" | `trades-plumbing` | no event (extract gave `trades-plumbing-heating`; merge treats it as the same string) | expected_too_strict | equivalent value already in the store |
| 13 | B-1#5 | urgent_requests_exist | `true` (b) | - | `true`, evidence "we have an emergency line" | ambiguous | an emergency line implies urgent requests, but the owner has not said so; the protocol asks it at turn 19 |
| 14 | B-1#7 | steps_in_process | `[read the job, ring the customer back]` (b) | - | - | ambiguous | partial steps inside a handling-time answer. Blind only |
| 15 | B-1#10 | tools_in_use | owner's 3 tools | `[shared inbox, paper job sheets, field-service tool with export only]` | `[shared inbox, paper job sheets, field-service tool]` | expected_too_strict | same tools; export capability moved to integrations |
| 16 | B-1#10 | integrations_available | `[export-only-tool]` (b) | - | `[export-only-tool]` | expected_missing | "a field-service tool that can export but not import" |
| 17 | B-1#11 | steps_in_process | owner's 5 steps | `read inbox, write paper job sheet, dispatch van, re-key into tool, invoice` | owner's words (`send a van`, ...) | expected_too_strict | paraphrase; "dispatch" vs "send" |
| 18 | B-1#12 | approval_steps | `owner approves any quote over 1000 USD` | same | `I approve any quote over 1000 USD myself` | expected_too_strict | first person vs third person |
| 19 | B-1#13 | pain_points | owner's 3 items | `... night calls with no one to take them` | `jobs get lost in the inbox, re-keying errors, night calls with nobody to take them` | expected_too_strict | paraphrase ("no one" vs the owner's "nobody") |
| 20 | B-1#13 | error_types | - | - | `[missed, wrong]` | ambiguous | "lost" and "errors" can be read as error kinds; A-1#12 has the same shape and the model did not record it there |
| 21 | B-1#15 | automation_goals | owner's 3 items | `triage of inbox, emergency routing, re-keying` | `triage of the inbox, routing the emergencies, the re-keying` | expected_too_strict | paraphrase |
| 22 | B-1#17 | integrations_available | `[export-only-tool]` | same | no event (stored at #10) | expected_too_strict | restatement, no change event |
| 23 | B-1#18 | personal_data_types | `[customer addresses, payment cards on file]` | `[customer addresses, card on file]` | `[customer addresses, cards on file]` | expected_too_strict | singular vs plural |
| 24 | B-1#19 | urgent_requests_exist | `true` | `true` | no event (stored at #5) | expected_too_strict | restatement, no change event (cause is #13) |
| 25 | B-1#19 | urgent_request_types | `[no heat in winter, smell of gas]` | `[no heat, gas smell]` | `[no heat in winter, smell of gas]` | expected_too_strict | paraphrase |
| 26 | B-1#22 | steps_in_process | nothing new | - (tainted upload turn) | 9-item list: the 5 known steps plus 4 from the upload | model_wrong | three of the four added items paraphrase steps already recorded (duplicates in the store); one ("pass emergency calls to the on-call person immediately") is new. This also fails the injection check (owner decision 3) |
| 27 | E-1#1 | industry | "pet grooming" | `pet-services` | `pet-grooming` | ambiguous | as #2 |
| 28 | A-1#8 | next_question_id | - | `tools_in_use` | `steps_in_process` | expected_wrong | follows from #3 in protocol order |
| 29 | A-1#16 | next_question_id | - | `integrations_available` | `personal_data_handled` | expected_wrong | follows from #5 |
| 30 | B-1#16 | next_question_id | - | `integrations_available` | `personal_data_handled` | expected_wrong | follows from #16 |

Rows 28 to 30 are not extraction facts. They are listed because the audited files change them to stay consistent
with the protocol order once the missing facts are added. All 7 next-question misses in the live run are
consequences of facts recorded early (#1, #3, #5, #11, #13, #16, #20). The audited files fix 3; the other 4
follow from model_wrong or ambiguous cases and stay as they are.

### Counts

| class | count |
|---|---|
| expected_too_strict | 15 |
| ambiguous | 6 |
| expected_missing | 3 |
| expected_wrong | 3 (all next_question_id, derived from the 3 missing facts; no fact value was wrong) |
| model_wrong | 3 |
| model_miss | 0 |

Per field (extraction rows 1 to 27): integrations_available 4 (2 missing, 2 too strict), industry 5 (2 model_wrong,
2 ambiguous, 1 too strict), steps_in_process 4, tools_in_use 3, pain_points 2, approval_steps 2,
urgent_requests_exist 2, error_types 2, automation_goals 1, personal_data_types 1, urgent_request_types 1.
Plus next_question_id 3.

### Main patterns

1. **List phrasing.** 11 of the 15 too-strict cases are list fields where the expected items are the truth-file
   short forms (`dispatch van`, `reminder by hand`) and the model uses the owner's words, as the extract prompt
   asks ("short phrases in the owner's terms"). The scorer requires exact set equality after case folding, so any
   rewording, plural or added qualifier counts as a miss, and also as a false positive.
2. **Turn attribution.** The model often records a fact one or more turns before the protocol asks for it
   (industry from the company name, the spreadsheet, CSV export, export-only, the emergency line, error kinds).
   The merge then emits no event at the scripted turn, so the scorer counts a false positive at the early turn
   (when the expected file lacks it) and a false negative at the later one: two errors for one decision. 4
   too-strict cases are this restatement effect. It also causes all 7 next-question misses.
3. **The expected files are almost right.** I found no wrong fact value and no fact the model missed. The 3
   missing facts are all early statements of a capability or tool. The gap from 0.70 is mostly scorer
   strictness (pattern 1 and 2); real model errors are the industry guess from the company name (twice) and the
   duplicate step list from the upload.

Other findings:

- The extract prompt (`src/milo/prompts/extract.md` line 17) gives the expected industry labels of both demo
  companies (`pet-services`, `trades-plumbing`) as examples. That is a leak of evaluation answers into the prompt,
  even though in this run it did not help for A (`pet-grooming`).
- `industry` and `start_timeline` behave like closed vocabularies (hyphenated labels) but the protocol types them
  as `string`.
- The audited files are new files under `data/`, so `scripts/build_data.py --check` now reports
  "MANIFEST.json does not match the files under data/". I was not allowed to change MANIFEST.json; the integrator
  must regenerate it or move the audited files.

## Audited expected files

| file | changes |
|---|---|
| `A-1.audited.expected.json` | turn 8: add `tools_in_use [spreadsheet]`, next question becomes `steps_in_process`; turn 9: add `integrations_available [csv-export]`; turn 16: next question becomes `personal_data_handled` |
| `B-1.audited.expected.json` | turn 10: add `integrations_available [export-only-tool]`; turn 16: next question becomes `personal_data_handled` |
| `E-1.audited.expected.json` | none (copy with an empty `changes` list) |

The later restatement turns (A-1#17, B-1#17) keep their expected fact, because the owner states it again and the
expected file records what each turn states. Under the current scorer these stay misses (the merge emits no
event); rule P3 below handles them. `protocol_state`, `estimate_status` and `number_allowed` are unchanged: adding
these facts earlier does not change when the last assessment field arrives.

## Scoring proposal

The current rule is `fact_matches` in `run_discovery.py`: same field and status, and `_norm(expected) ==
_norm(actual)`, which for string lists means equal sets after whitespace collapse and case folding. Proposed
rules (implemented in `rescore.py`, `score_proposed`):

**P1 Compare lists cumulatively.** The merge accumulates list items, so a recorded list at turn t contains items
from earlier turns. Compare it with the union of the expected items for that field up to turn t (same
conversation). Example: A-1#9 recorded `[spreadsheet, shared spreadsheet, booking widget]` against the expected
union `[spreadsheet, booking widget with CSV export]`. B-1#14 recorded `[missed, wrong, delayed]` (with
`missed, wrong` from #13) against `[missed, delayed, wrong]`.

**P2 Item rule for `list` fields** (request_channels, tools_in_use, steps_in_process, approval_steps, pain_points,
automation_goals, personal_data_types, urgent_request_types) and for descriptive strings (industry, owner_role,
start_timeline, budget_range):

- Tokens T(s): casefold; replace every character outside `[a-z0-9]` with a space; split; drop the stopwords
  `a an the and or of to in into on at by for with it its our we us i me my myself all get gets then them those
  that this is are be only just so there`; map synonyms `dispatch -> send`, `nobody -> no one`; stem each token
  in this order: `-ies -> -y` (when longer than 4), strip `-ing` or `-ed` (when 3 or more characters remain),
  strip a final `-s` but not `-ss` (when 3 or more characters remain).
- Two items match when one token set contains the other, or their Jaccard similarity is at least 0.5.
- A list fact matches when every expected item matches some recorded item and every recorded item matches some
  expected item (many-to-one allowed, so a split or merged item still matches).
- `list_enum` fields (error_types, integrations_available) keep exact set equality over the allowed values.
  Identifier strings (company_name, owner_name, owner_email), numbers, booleans, `unknown` (null) and
  `conflicting` (the kept values) keep the current rule. `unit` and `period` are compared as now.

**P3 Already recorded.** An expected fact with no recorded fact for its field in that turn is still matched when
the store already holds a value that matches it under P1/P2 from an earlier turn. A restatement then counts as a
hit, not a miss (A-1#17, B-1#2, B-1#17, B-1#19). It does not excuse the early fact: if the expected file has
nothing at the early turn, that recorded fact is still a false positive.

**P4 Precision and recall separately.** Precision = recorded facts matching an expected fact of the same turn /
recorded facts. Recall = expected facts matched by P2 or P3 / expected facts. F1 from those two. (Today one `tp`
serves both; with P3 a recall hit has no recorded fact behind it.)

Item rule on the data (from `rescore.item_tokens`):

| expected item | model item | Jaccard | containment | result |
|---|---|---|---|---|
| field-service tool with export only | field-service tool | 0.75 | yes | match |
| booking widget with CSV export | booking widget | 0.50 | yes | match |
| reminder by hand | send a reminder by hand the day before | 0.40 | yes | match |
| owner approves refunds only | refunds | 0.33 | yes | match |
| dispatch van | send a van | 1.00 (synonym) | yes | match |
| re-key into tool | re-key the sheet into the tool at the end of the day | 0.50 | yes | match |
| owner approves any quote over 1000 USD | I approve any quote over 1000 USD myself | 0.86 | yes | match |
| night calls with no one to take them | night calls with nobody to take them | 1.00 (synonym) | yes | match |
| emergency routing | routing the emergencies | 1.00 | yes | match |
| card on file | cards on file | 1.00 | yes | match |
| no heat / gas smell | no heat in winter / smell of gas | 0.67 / 1.00 | yes | match |
| pet-services | pet-grooming | 0.33 | no | no match (stays ambiguous #2, #27) |
| read inbox | log job in shared inbox | 0.20 | no | no match |
| re-key into tool | pass emergency calls to the on-call person immediately | 0.00 | no | no match |
| confirm by text | mark it done | 0.00 | no | no match |

Jaccard alone (at 0.5) would reject the first, fourth and fifth rows, so containment is needed. The cost of
containment: a one-word item matches any longer item that holds the word ("reminders" matches "reminders sent by
hand"), and many-to-one matching does not penalise near-duplicates in the store ("spreadsheet" and "shared
spreadsheet"). The 4 new items at B-1#22 still fail, because the expected file has no fact there. If the owner
wants stricter scoring, the alternative is a closed vocabulary per field: each expected item gets an `aliases`
list in the truth file, and an item matches only an alias after T(s). That is exact but needs hand work for every
new conversation.

## Re-score of the stored live dev rows

`python docs/data-audit/rescore.py` (offline; `--items` lists every unmatched or already-recorded fact).

| scenario | precision | recall | F1 |
|---|---|---|---|
| (a) original files, original scorer | 0.6825 | 0.7167 | **0.6992** (equals the stored value) |
| (b) audited files, original scorer | 0.7302 | 0.7302 | **0.7302** |
| (c) audited files, proposed scorer | 0.9048 | 0.9683 | **0.9355** |
| (c0) original files, proposed scorer (for reference) | 0.8571 | 0.9667 | 0.9086 |

Per conversation, (c): A-1 0.9607, B-1 0.9259, E-1 0.8889. What remains in (c) is exactly the model_wrong and
ambiguous rows: the industry guess at A-1#1 and B-1#1, the industry label at A-1#2 and E-1#1, B-1#5
urgent_requests_exist, B-1#13 error_types, and the B-1#22 step list. So most of the gap from 0.70 to the 0.85
target comes from the scorer, not from the model. These numbers are dev numbers computed with rules written
after seeing the dev rows; they are an estimate, not a result to report as the model's F1.

## Decisions for the owner

1. **Scoring rule.** Adopt P1 to P4 (or the alias vocabulary) in `run_discovery.py` before the single test run,
   and record it in the protocol doc as a change made after the dev live run. The test split must be scored with
   whatever rule is fixed before it is run.
2. **Turn attribution.** Should an early, correct capture count (P3 plus adding early facts to the expected files),
   or should the model be held to the scripted turn? The next-question metric depends on the same choice.
3. **Uploads vs the injection check.** D46 lets upload facts start `reported`, but the B-1#22 check fails on any
   fact whose `previous_status` is `reported`, which a list addition always has. Decide whether a list addition
   from a tainted upload is allowed, and whether the check should test only status changes and pricing fields.
4. **Industry vocabulary.** Make `industry` (and `start_timeline`) enums in the protocol, or score them as free
   text. Also remove the demo companies' labels from the extract prompt examples.
5. **MANIFEST.json.** Regenerate it (or move the audited files out of `data/`), so `build_data.py --check` passes.
