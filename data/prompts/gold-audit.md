# Gold audit (M0b), first pass

| Field | Value |
|---|---|
| Auditor | Gold audit role (slug `audit`), a session Claude agent separate from the generator |
| Date | 2026-10-22 |
| Commit audited | `a7ab661` (M0b: gold expansion to 120 rows) |
| Branch | `agent/audit/m0b` |
| Protocol | `m0b-gold-expansion` (`evals/protocols/m0b-gold-expansion.md`), Step 3 checklist |
| Inputs read | SPEC sections 3 and 10.1, D22, D60, D66, D78, D126, D151; HARNESS rules 6 and 7; all 24 articles under `data/kb/`; `data/source/catalogue.yaml`; the 120 rows of the gold file (dev and test) |
| Not read | the generator's notes and chat, `## gold expansion (M0b)` in `generation-record.md`, `.env` |
| Calls | no model call, no network call (D126) |
| Files written | this file only; no data was edited |

## 1. Script outputs

All checks below were run with a read-only Python script over the committed files (no library beyond the
standard library). Quote integrity is tested as: exact substring of the article body (text after the front
matter), occurring in exactly one level-2 heading section, at most 150 characters.

```
rows: 120
sha256 gold: 7a47a9be5faeb500d43ed29af81735941a95e644bb255634a20d0b3a64afedec
manifest  : 7a47a9be5faeb500d43ed29af81735941a95e644bb255634a20d0b3a64afedec
articles: 24

== quote integrity and filters ==
quotes verified (exact substring, one heading, <=150 chars): 96 of 96
max quote length: 144
problems: none
unique qids: True | contiguous q001..q120: True

== identifier rows naming a catalogue id ==
identifier rows without an id in the question: none
paraphrase rows that mention a catalogue id literally: none

== duplicates ==
equal after normalisation: none
word-set Jaccard > 0.6 (whole file): none
highest Jaccard pairs overall: [('q038', 'q109', 0.3), ('q082', 'q084', 0.27), ('q061', 'q105', 0.27),
  ('q005', 'q049', 0.27), ('q062', 'q093', 0.26), ('q013', 'q015', 0.26), ('q072', 'q116', 0.25), ('q032', 'q108', 0.25)]

== cross-split leakage ==
quotes used by more than one row: none
quotes shared across dev and test: none
(doc, heading) groups with both dev and test rows: 4
    ('cases/grooming-bookings', 'What it did not do') [('q053', 'dev'), ('q085', 'test')]
    ('consultation/urgent-requests', 'How the modules treat urgent requests') [('q055', 'test'), ('q056', 'dev')]
    ('pricing/how-we-price', 'What the assistant may and may not do') [('q014', 'dev'), ('q071', 'test')]
    ('pricing/volume-tiers', 'How to read it') [('q017', 'test'), ('q075', 'dev')]
cross-split question Jaccard >= 0.3: [('q038', 'q109', 0.3)]
identical reference answers across rows: none

== stratification ==
identifier    dev= 12 test= 12 total= 24
paraphrase    dev= 36 test= 36 total= 72
unanswerable  dev= 12 test= 12 total= 24
total         dev= 60 test= 60
with a filter: dev=22 test=22 total=44
filter keys: {'product': 14, 'section': 30}
per-article answerable rows (dev/test/total): every one of the 24 articles is 2/2/4
articles out of quota: none
rows with more than one evidence item: none

== safety ==
safety hits (D22 phrases, demo company or owner names, key, path, email or URL patterns): none
D22 phrases in KB bodies: [('policies/security', 'mark every field'), ('policies/security', 'set the price to zero')]

== question-quote word overlap (share of question words that appear in the quote) ==
rows above 0.20: 34 rows; 24 of them identifier rows (the shared words are the catalogue id itself),
  highest paraphrase rows: q019 0.33, q013 0.31, q011 0.27, q067 0.26, q064 0.25

== realism quotas on the 80 new rows (q041-q120) ==
fragments (<=5 words): 14 ['q050','q074','q075','q080','q085','q088','q094','q102','q103','q106','q107','q110','q114','q118']
multi-part (two '?' or 'and <wh/aux>' by script): 10 ['q059','q063','q066','q068','q076','q078','q079','q086','q090','q099']
typo/short form (token list): 28
```

Unanswerable rows, keyword and synonym search over the 24 article bodies (synonym lists chosen by the
auditor per row, every hit read in context):

```
q033 languages: no hits            q105 whatsapp/sms/messaging: no hits
q034 trial/free/try: "free that afternoon" only   q106 landline/voip: "phone number ... bought separately" (not a channel type)
q035 mobile app/van: no app        q107 2fa/two-factor/mfa: no hits ("code" = "in code")
q036 countries/region: no hits     q108 server/eu/hosting: "the hosting" is listed as included, no location
q037 uptime/sla: no hits           q109 direct debit/bank/payment: customer card payments only
q038 annual/yearly/billing: no payment schedule     q110 demo/video/trial: no hits
q039 training/onboard: no hits     q111 voice/accent/tone: "voicemail", "tone" of templates only
q040 contract/notice/term: no hits ("cancellation policy" is the salon's own)   q112 attachment/photo: upload rule for the interview only
                                   q113 calendar/sync: "slots you have opened", no calendar
                                   q114 logo/brand: no hits
                                   q115 insur/liabil/guarantee: "do not promise a saving" only
                                   q116 referral/commission: no hits
                                   q117 remote/on-site/visit/travel: "prices exclude ... travel" only, no answer
                                   q118 weekend/out of hours/support: see row verdict
                                   q119 founded/since/history: no hits
                                   q120 rating/feedback/survey: no hits
```

## 2. Standards applied

- Answerability: the quote, read with the question and knowing which article it comes from, must yield the
  reference answer without further text. A quote whose grammatical subject is a bare pronoun ("it")
  with its antecedent outside the quote needs the heading or the surrounding sentence to be understood and
  gets `fix` with a replacement quote, from the same article where one exists within 150 characters;
  where none exists the row says so and proposes the closest option. Nine rows fall under this rule
  (q010, q014, q046, q049, q057, q059, q089, q096, q100). A quote with an elided noun subject
  ("Setup takes one working day.") or a definite noun phrase ("The module ...") that the article title
  resolves is `ok`. A quote that starts mid-sentence is `ok` when the fragment states the fact without
  distortion (q052, q070, q088) and `fix` when the truncation changes the meaning (q047).
- Reference answers were checked row by row for polarity against the quote (yes/no agreement); one
  inversion was found (q054).
- Style, spelling and tone are not grounds for `fix`.
- Leakage: a dev and test pair is a problem only when it asks the same fact from the same quote, or when
  a test question is a rewording of a dev question. Same-topic pairs from different articles are listed
  under "Leakage findings" with the reason they are cleared.

## 3. Row verdicts

Column "changed" is the "changed since first pass" column the Decision rule asks for; it is `-` for every
row in this first pass.

| qid | type/split | verdict | changed | notes |
|---|---|---|---|---|
| q001 | identifier/dev | ok | - | Quote gives one excluded activity; reference says "among others". The full exclusion sentence pair (146 chars) is available in the same heading if the generator wants the complete list. |
| q002 | identifier/test | ok | - | |
| q003 | identifier/dev | ok | - | |
| q004 | identifier/test | ok | - | |
| q005 | identifier/dev | ok | - | Same fact as q087 (test) from a different article and quote; see Leakage findings. |
| q006 | identifier/test | ok | - | |
| q007 | identifier/dev | ok | - | Same fact as q054 (test) from a different article; see Leakage findings. |
| q008 | identifier/test | ok | - | |
| q009 | paraphrase/dev | ok | - | |
| q010 | paraphrase/dev | fix | - | Pronoun subject: "Before it creates a ticket it checks for duplicates ..." names nothing. No sentence under "What it does" names the module within 150 chars. Proposed: same article, heading "What is included", quote `The module includes ticket fields, a duplicate check, status tracking and reminders to staff.` (93 chars); reference "A duplicate check is part of the module, run before a ticket is created." Alternative: keep the quote and reword the question to name its subject without a catalogue id, e.g. "How does the ticket module stop one job being filed twice?". |
| q011 | paraphrase/test | ok | - | |
| q012 | paraphrase/dev | ok | - | |
| q013 | paraphrase/test | ok | - | |
| q014 | paraphrase/dev | fix | - | Pronoun subject: "It cannot change the arithmetic ...". Proposed quote, same heading: `The assistant may suggest which catalogue modules fit what you described. It cannot change the arithmetic, apply a discount, or set a price itself.` (147 chars). Reference unchanged. |
| q015 | paraphrase/test | ok | - | |
| q016 | paraphrase/dev | ok | - | |
| q017 | paraphrase/test | ok | - | Template sibling of q075 (dev): same question shape, different tier and different quote; see Leakage findings. |
| q018 | paraphrase/dev | ok | - | |
| q019 | paraphrase/test | ok | - | Question-quote overlap 0.33, the highest paraphrase row; the shared words are "field mapping" and "CRM". Not a copy. |
| q020 | paraphrase/dev | ok | - | |
| q021 | paraphrase/test | ok | - | |
| q022 | paraphrase/dev | ok | - | |
| q023 | paraphrase/test | ok | - | Elided subject ("Setup takes one working day."); the question names the API and the doc is the API connector. The generator may prefer `Your tool must offer an API with an access key. Setup takes one working day.` (78 chars); not required. |
| q024 | paraphrase/dev | ok | - | Same pattern as q023; no fuller sentence available in the article. |
| q025 | paraphrase/test | ok | - | Same limit as q052 (dev) from a different article; see Leakage findings. |
| q026 | paraphrase/test | ok | - | |
| q027 | paraphrase/dev | ok | - | |
| q028 | paraphrase/test | ok | - | |
| q029 | paraphrase/dev | ok | - | |
| q030 | paraphrase/test | ok | - | |
| q031 | paraphrase/dev | ok | - | |
| q032 | paraphrase/test | ok | - | |
| q033 | unanswerable/dev | ok | - | No article mentions languages. |
| q034 | unanswerable/test | ok | - | No trial, free period or demo in any article. |
| q035 | unanswerable/dev | ok | - | No mobile app anywhere. |
| q036 | unanswerable/test | ok | - | No country or region named. |
| q037 | unanswerable/dev | ok | - | "What we do not claim" in the security policy makes no uptime statement. |
| q038 | unanswerable/test | ok | - | No payment schedule anywhere. Jaccard 0.30 with q109 (dev), both unanswerable, different topic (annual payment vs direct debit); see Leakage findings. |
| q039 | unanswerable/dev | ok | - | The setup fee covers "checking the first results with you", not staff training. |
| q040 | unanswerable/test | ok | - | No contract term or notice period; "cancellation policy" in the salon case is the salon's own. |
| q041 | identifier/test | ok | - | |
| q042 | identifier/dev | ok | - | |
| q043 | identifier/test | ok | - | |
| q044 | identifier/dev | ok | - | |
| q045 | identifier/dev | ok | - | "the module" resolved by the question ("customer updates") and the article. |
| q046 | identifier/test | fix | - | Pronoun subject: "For staff-assignment it is the only integration offered." ("it" = csv-export). The csv-export article has no sentence naming the subject within 150 chars (the module list sentence is 164). Proposed: doc `solutions/staff-assignment`, heading "Connecting it to your tools", quote `The module works with csv-export. Skill tags and availability can be imported from a spreadsheet, and the assignments can be exported to one.` (141 chars); filter becomes `{"product": "staff-assignment"}` (section=implementation would no longer hold). Reference "No. It works with csv-export: skill tags and availability come in from a spreadsheet and assignments go out to one." Effect on quotas: section 30 to 29, product 14 to 15 (both at least 12); csv-export article 4 to 3 answerable rows (2 dev, 1 test), staff-assignment 4 to 5 (2 dev, 3 test); filtered test count unchanged. Alternative: keep the row and reword the question to "can staff-assignment hook into anything besides csv-export?", which names the antecedent. |
| q047 | identifier/test | fix | - | Quote starts mid-conditional: "the delay is acceptable, csv-export is a cheaper way ..." read alone asserts the delay is acceptable. The full sentence is 151 chars. Proposed quote `csv-export is a cheaper way to start, and you can move to the connector later.` (78 chars); reference "Yes. csv-export is a cheaper way to start, and you can move to the connector later." |
| q048 | identifier/dev | ok | - | |
| q049 | identifier/test | fix | - | Pronoun subject: "It is not offered for staff-assignment ..." ("It" = api-connector). Proposed quote, same heading: `The api-connector is available for phone-intake, email-triage, ticket-creation and customer-status-updates.` (107 chars); reference "No. The api-connector is available for phone-intake, email-triage, ticket-creation and customer-status-updates; document-data-entry is not in the list." This answers by omission; if the generator prefers the explicit sentence, rewording the question to "can document-data-entry run over api-connector?" names the antecedent and the quote may stay. |
| q050 | identifier/dev | ok | - | |
| q051 | identifier/test | ok | - | |
| q052 | identifier/dev | ok | - | Mid-sentence fragment that states the fact without distortion; `the report said plainly that re-keying ...` (132 chars) is available if the generator wants a sentence start. |
| q053 | identifier/dev | ok | - | |
| q054 | identifier/test | fix | - | Reference answer polarity is inverted: the question asks "did csv-export cost them anything extra?" and the answer starts "Yes." while the quote says "available at no surcharge". Proposed reference: "No. Their system could import CSV files, so csv-export was available at no surcharge." Quote and labels unchanged. |
| q055 | identifier/test | ok | - | |
| q056 | identifier/dev | ok | - | |
| q057 | paraphrase/test | fix | - | Pronoun subject: "If the request needs a person, it writes a handover note ...". No fuller sentence under "What it does" fits 150 chars. Proposed: same article, heading "What is included", quote `The module includes call answering, request capture, appointment booking, a call transcript for each call, and handover to staff.` (129 chars); reference "No. Handover to staff is part of the module." This loses "handover note"; alternative: keep the quote and reword the question so the subject is named, e.g. "what if the caller has something the phone bot cant deal with, does it just hang up on them?". |
| q058 | paraphrase/dev | ok | - | |
| q059 | paraphrase/test | fix | - | Pronoun subject in both question and quote ("It does not change the mailbox rules ..."). Proposed quote, same heading: `The email-triage module never sends a message without approval. It does not change the mailbox rules, and it does not delete mail.` (130 chars). This contains q002's quote verbatim; q002 is also test, so no cross-split sharing results. Reference unchanged. |
| q060 | paraphrase/test | ok | - | |
| q061 | paraphrase/dev | ok | - | |
| q062 | paraphrase/test | ok | - | |
| q063 | paraphrase/dev | ok | - | |
| q064 | paraphrase/test | ok | - | |
| q065 | paraphrase/test | ok | - | |
| q066 | paraphrase/test | ok | - | |
| q067 | paraphrase/dev | ok | - | |
| q068 | paraphrase/dev | ok | - | |
| q069 | paraphrase/dev | ok | - | |
| q070 | paraphrase/dev | ok | - | Fragment list; the full sentence is 171 chars, so the fragment is forced. States the four inputs without distortion. |
| q071 | paraphrase/test | ok | - | |
| q072 | paraphrase/dev | ok | - | |
| q073 | paraphrase/test | ok | - | |
| q074 | paraphrase/dev | ok | - | |
| q075 | paraphrase/dev | ok | - | Template sibling of q017 (test); see Leakage findings. |
| q076 | paraphrase/test | ok | - | |
| q077 | paraphrase/dev | ok | - | |
| q078 | paraphrase/test | ok | - | |
| q079 | paraphrase/dev | ok | - | |
| q080 | paraphrase/test | ok | - | |
| q081 | paraphrase/dev | ok | - | Filter product=ticket-creation matches the case article's front matter. |
| q082 | paraphrase/test | ok | - | |
| q083 | paraphrase/test | ok | - | |
| q084 | paraphrase/dev | ok | - | |
| q085 | paraphrase/test | ok | - | |
| q086 | paraphrase/dev | ok | - | |
| q087 | paraphrase/test | ok | - | Same fact as q005 (dev) from a different article; see Leakage findings. |
| q088 | paraphrase/dev | ok | - | Mid-sentence fragment; states the fact without distortion. |
| q089 | paraphrase/test | fix | - | Pronoun subject: "It never decides on its own that a topic is finished." Proposed quote, same heading: `The assistant may phrase an acknowledgement in its own words, but the question it asks next comes from the list, word for word.` (127 chars); reference "No. The next question always comes from the fixed list, word for word." The "never decides" sentence cannot be joined to a named subject within 150 chars. |
| q090 | paraphrase/dev | ok | - | |
| q091 | paraphrase/test | ok | - | |
| q092 | paraphrase/dev | ok | - | |
| q093 | paraphrase/test | ok | - | |
| q094 | paraphrase/dev | ok | - | |
| q095 | paraphrase/test | ok | - | |
| q096 | paraphrase/dev | fix | - | Pronoun subject: "It does not ask for and does not store any actual customer record." and the question says "for this". The preceding sentence is too long to join. Proposed quote, same heading: `If you paste a customer's details into the conversation, we ask you not to, and the operator can delete the message on request.` (127 chars); reference "No. The interview wants no customer records; if you paste a customer's details we ask you not to." Alternative: keep the quote and reword the question to name the subject, e.g. "do i have to upload my client list for the interview?". |
| q097 | paraphrase/test | ok | - | "milo" in the question is the fictional consultancy, not a real company. |
| q098 | paraphrase/dev | ok | - | "Until then" is resolved by the question (consent box). |
| q099 | paraphrase/test | ok | - | |
| q100 | paraphrase/dev | fix | - | Pronoun subject: "In a suspicious turn it will not record a fact ...". The approvals article has no fitting line within 150 chars. Proposed: doc `policies/security`, heading "Tools the assistant can use", quote `In a turn that looks like it contains an injected instruction, destructive actions are refused outright and price-related facts are not written.` (144 chars); reference "Destructive actions are refused outright and price-related facts are not written." No filter on this row. Effect on coverage: approvals 4 to 3 (1 dev, 2 test), security 4 to 5 (3 dev, 2 test), both inside 3 to 6. |
| q101 | paraphrase/test | ok | - | |
| q102 | paraphrase/dev | ok | - | |
| q103 | paraphrase/test | ok | - | |
| q104 | paraphrase/dev | ok | - | |
| q105 | unanswerable/dev | ok | - | No messaging channel named anywhere; phone-intake handles calls only. |
| q106 | unanswerable/test | ok | - | "If a module needs a phone number ... bought separately" does not say landline or VoIP. |
| q107 | unanswerable/dev | ok | - | Sign-in is by invitation link and password; no second factor mentioned. |
| q108 | unanswerable/test | ok | - | "the hosting" is listed as included with no location. |
| q109 | unanswerable/dev | ok | - | Payment means for the owner are never stated. |
| q110 | unanswerable/test | ok | - | |
| q111 | unanswerable/dev | ok | - | |
| q112 | unanswerable/test | ok | - | The PDF/text rule is for interview uploads, not email-triage attachments. |
| q113 | unanswerable/dev | ok | - | "books one in the slots you have opened" says nothing about calendar sync. |
| q114 | unanswerable/test | ok | - | |
| q115 | unanswerable/dev | ok | - | |
| q116 | unanswerable/test | ok | - | |
| q117 | unanswerable/dev | ok | - | "Prices exclude taxes, travel and any hardware" implies travel may occur but does not answer remote vs on-site. Borderline; kept. |
| q118 | unanswerable/test | fix | - | "weekend support?" is ambiguous: as vendor support hours it is absent from every article, but as module coverage it is answered by phone-intake ("answers incoming calls out of hours", "bookings taken when the shop is closed") and the out-of-hours section of the urgent-requests article, so a retriever or judge can reasonably treat it as answerable. Proposed question: "weekend support from your team?" (5 words, keeps the fragment tag). Type and split unchanged. |
| q119 | unanswerable/dev | ok | - | |
| q120 | unanswerable/test | ok | - | |

## Summary

| Verdict | Count | qids |
|---|---|---|
| ok | 108 | all rows not listed below |
| fix | 12 | q010, q014, q046, q047, q049, q057, q059, q089, q096, q100 (quote; q046 and q100 also doc or filter), q054 (reference answer), q118 (question) |
| remove | 0 | none |

Of the 12 `fix` rows, 9 are the pronoun-subject rule (q010, q014, q046, q049, q057, q059, q089, q096,
q100), 1 is a truncated conditional (q047), 1 is an inverted reference answer (q054), 1 is an ambiguous
unanswerable question (q118). Three of the pronoun rows (q010, q049, q057) have only a weaker replacement
available, and for each an alternative question reword is given; the generator chooses. If the generator
applies the proposed doc changes for q046 and q100, every count in the Quota check still holds (worked
out in the row notes). Split assignments are not moved by any proposal.

## Leakage findings

Script: no quote is used by more than one row, so no dev and test pair shares a quote; no cross-split
pair of questions has word-set Jaccard above 0.30 (the only pair at 0.30 is two unanswerable rows); no two
reference answers are identical. By reading, every candidate pair was cleared:

| Pair (dev, test) | What they share | Why cleared |
|---|---|---|
| q005 dev, q087 test | handwriting outside the agreed form types is not read | different articles (solutions vs case), different quotes, q087 asks about the case outcome |
| q007 dev, q054 test | csv-export has no surcharge | different articles (implementation vs case), different quotes |
| q052 dev, q025 test | export-only tools cannot have re-keying removed | different articles, q025 asks whether an import can be added, q052 what the case report said |
| q075 dev, q017 test | volume-factor lookup, same question shape | different tier (100 vs 50 a day), different quote and answer (1.50 vs 1.25); knowing one does not give the other |
| q090 dev, q076 test | monthly volume converted to daily | q090 asks how a derived fact is labelled (estimated), q076 whether monthly figures are accepted; different articles |
| q079 dev, q026 test | blocked estimate | q079 asks what blocked shows (pricing article), q026 why the case firm saw no price (case article) |
| q094 dev, q082 test | start date and estimate firmness | q094: price does not change; q082: why the case estimate was a scenario; different articles |
| q014 dev, q071 test | same heading of how-we-price | discount vs estimate panel; different facts |
| q053 dev, q085 test | same heading of the grooming case | deposits vs no-shows; different facts |
| q056 dev, q055 test | same heading of urgent-requests | email label vs phone handover; different facts |
| q109 dev, q038 test | how the owner pays (both unanswerable) | direct debit vs annual billing; no fact to leak, both INSUFFICIENT_CONTEXT |

No test question is a rewording of a dev question. The proposed fixes keep this: q059's replacement
contains q002's quote, and both rows are test.

## Quota check

Stratification table of the protocol against the file (script output above):

| Type | Required final (dev/test) | Found (dev/test) | Met |
|---|---|---|---|
| identifier | 24 (12/12), at least 8 per split | 24 (12/12) | yes |
| paraphrase | 72 (36/36), split differs by at most 1 | 72 (36/36) | yes |
| unanswerable | 24 (12/12), at least 12 per split | 24 (12/12) | yes |
| total | 120 (60/60) | 120 (60/60) | yes |
| with a filter | 44 (22/22), at least 40 and at least 20 dev | 44 (22/22) | yes |

Secondary quotas: every article has 4 answerable rows (2 dev, 2 test), inside 3 to 6 with at least 1 per
split; filter keys are `section` (30) and `product` (14) only, each at least 12 (D151); every filtered
row's evidence document satisfies the filter against its front matter; no `updated_after` filter.

Realism quotas on the 80 new rows: fragments of five words or fewer 14 (quota 10); multi-part questions
answered by one quote 10 by script and 13 by reading (adds q057, q105, q108; quota 10); typo or short
form 28 by token list (quota 16); owner answering in fragments 16 by reading (q041, q042, q047, q064,
q065, q071, q074, q075, q077, q078, q079, q088, q094, q095, q098, q102; quota 8). All four quotas hold.
The typos and short forms read as natural owner shorthand ("ppl", "thru", "atm", "montly", "yr"), not
random noise. The 40 old rows are more formal than the new ones; this is style and not flagged.

Question-quote overlap: 24 identifier rows and 10 paraphrase rows exceed the protocol's 20 percent word
share by the auditor's raw measure (all words counted). For identifier rows the shared words are the
catalogue id the question must name; the highest paraphrase row is 0.33. No paraphrase row copies its
quote. Treated as style; the builder's own check passed at the generation commit.

## Notes outside the gold rows

- `data/kb/policies/security.md` ("Text is data, not instructions") contains the D22 phrases
  "mark every field" and "set the price to zero". D66 says no article contains a D22 phrase and that this
  article is reworded in M0; `scripts/build_data.py` has no check over `data/kb/**/*.md` for the D22 list.
  No gold row quotes those sentences. This is for the data role, not a gold verdict.

## Hash

SHA-256 of `data/gold.jsonl` at commit `a7ab661`:
`7a47a9be5faeb500d43ed29af81735941a95e644bb255634a20d0b3a64afedec`.
It equals the `data/gold.jsonl` entry in `data/MANIFEST.json`: yes.

---

# Pass 2 (protocol Step 4, after fix cycle 1)

| Field | Value |
|---|---|
| Auditor | Gold audit role (slug `audit`), same agent as pass 1 |
| Date | 2026-10-22 |
| Commit audited | `4f3bd8f` (M0b: fix cycle 1), merged into `agent/audit/m0b` as `419d25b` |
| Inputs | the 120 rows of the gold file, the 24 articles and `catalogue.yaml` (unchanged since `a7ab661`, checked with git), SPEC and the protocol as in pass 1 |
| Not read | the generator's notes and chat, `## gold expansion (M0b)` in `generation-record.md`, `.env` |
| Calls | no model call, no network call (D126) |
| Files written | this file only; no data was edited |

## Pass 2 script outputs

Same read-only script as pass 1, rerun on the fixed file.

```
rows: 120
sha256 gold: 6141c7e3e1a42066128b5a0323c032a5f44d704e670c7f07f8a58cb79aa37a72
manifest  : 6141c7e3e1a42066128b5a0323c032a5f44d704e670c7f07f8a58cb79aa37a72
articles: 24 (no change to data/kb or data/source/catalogue.yaml since a7ab661)

== quote integrity and filters ==
quotes verified (exact substring, one heading, <=150 chars): 96 of 96
max quote length: 147
problems: none
unique qids: True | contiguous q001..q120: True
identifier rows without an id in the question: none
paraphrase rows that mention a catalogue id literally: none

== duplicates ==
equal after normalisation: none
word-set Jaccard > 0.6 (whole file): none
highest Jaccard pairs overall: [('q005','q049',0.31), ('q038','q109',0.3), ('q082','q084',0.27), ('q061','q105',0.27),
  ('q062','q093',0.26), ('q013','q015',0.26), ('q072','q116',0.25), ('q032','q108',0.25)]

== cross-split leakage ==
quotes used by more than one row: none
quotes shared across dev and test: none
(doc, heading) groups with both dev and test rows: 5 (the four of pass 1 plus
    ('solutions/ticket-creation', 'What is included') [('q010','dev'), ('q060','test')])
cross-split question Jaccard >= 0.3: [('q005','q049',0.31), ('q038','q109',0.3)]
identical reference answers across rows: none

== stratification ==
identifier    dev= 12 test= 12 total= 24
paraphrase    dev= 36 test= 36 total= 72
unanswerable  dev= 12 test= 12 total= 24
total         dev= 60 test= 60
with a filter: dev=22 test=22 total=44
filter keys: {'product': 14, 'section': 30}
per-article answerable rows: every one of the 24 articles is 2/2/4
articles out of quota: none

== safety ==
safety hits: none
```

## Pass 2: the twelve fixed rows (full checklist)

Each row was rechecked for answerability, labels (type, filters against front matter, split), quote
integrity, duplicates, cross-split leakage, realism and safety. Where the generator departed from the
literal pass 1 proposal, the alternative is judged on its merits.

| qid | What changed | Judgement | verdict |
|---|---|---|---|
| q010 | quote replaced by the "What is included" sentence (93 chars), reference adjusted | as proposed; subject named; quote answers the question; new same-heading pair with q060 (test) asks a different fact (reminders), cleared | ok |
| q014 | quote extended to the 147-char two-sentence form | as proposed; subject named | ok |
| q046 | question reworded to "besides the csv export?"; quote, doc and filter kept | departs from the proposal: naming the antecedent in the question was the alternative offered in pass 1; "it" now resolves to csv-export from the question; per-article 2/2 preserved; identifier id `staff-assignment` still in the quote | ok |
| q047 | quote cut to the 78-char clause, "if the delay is acceptable" dropped from the reference | as proposed; no truncated conditional remains | ok |
| q049 | question reworded to "can document data entry run over api-connector?"; quote replaced by the 107-char list sentence; reference answers by omission | departs from the proposal on the question: the identifier rule needs every id in the question to appear in the quote, so `document-data-entry` was unhyphenated and `api-connector` added; the quote names its subject and lists the four supported modules, so the answer follows by omission; Jaccard with q005 (dev) is 0.31 on function words ("can document data entry"), different fact, cleared | ok |
| q054 | reference answer polarity corrected to "No." | as proposed | ok |
| q057 | quote replaced by the "What is included" sentence (129 chars), reference adjusted | as proposed; weaker evidence ("handover to staff") but it answers "do they just get hung up on?"; filter product=phone-intake still satisfied | ok |
| q059 | question reworded to "will the email bot mess with those?"; quote kept | departs from the proposal because the proposed quote contained q002's quote; the question now names the antecedent, so "It" resolves; the reworded question keeps both parts (multi-part tag intact) | ok |
| q089 | quote replaced by the 127-char "question it asks next comes from the list" sentence, reference adjusted | as proposed | ok |
| q096 | quote replaced by the 127-char "If you paste a customer's details" sentence, reference adjusted | as proposed; answers "do i have to upload my client list?" with no | ok |
| q100 | question reworded to "what does the assistant refuse to do with that message?"; quote and doc kept | departs from the proposal (moving to policies/security would break the 2/2 per-article rule); the question now names the antecedent of "it"; the quote states both refusals | ok |
| q118 | question reworded to "weekend support from your team?" | as proposed; vendor support hours are absent from every article (keyword search unchanged, articles unchanged); 5 words, fragment tag intact; still unanswerable, test | ok |

All twelve: quotes are exact substrings inside one heading and at most 150 characters; filters match
front matter; splits unchanged; no new duplicate or cross-split pair above 0.31; no D22 phrase, name,
key or path.

## Pass 2: spot check of 20 unchanged rows

Sample: `random.seed(0); random.sample(unchanged_qids, 20)` over the 108 unchanged qids, sorted:
q006, q015, q020, q030, q036, q039, q041, q051, q056, q060, q062, q070, q071, q073, q074, q083, q108,
q109, q112, q119. Each reread against its article with the full checklist (unanswerable rows against the
keyword search). All twenty: `ok`, unchanged since pass 1.

## Pass 2: changed since first pass, all 120 qids

| changed | qids |
|---|---|
| yes (quote) | q010, q014, q047, q057, q089, q096 |
| yes (quote and question) | q049 |
| yes (question only) | q046, q059, q100, q118 |
| yes (reference answer only) | q054 |
| no | q001-q009, q011-q013, q015-q045, q048, q050-q053, q055, q056, q058, q060-q088, q090-q095, q097-q099, q101-q117, q119, q120 (108 rows) |

Verdict in pass 2 for every one of the 120 qids: `ok`.

## Pass 2: leakage findings

Script: no quote shared across rows; no cross-split pair above Jaccard 0.31. New pairs introduced by the
fixes, read and cleared: q005 (dev) and q049 (test), 0.31, handwriting limit vs api-connector support,
different articles and quotes; q010 (dev) and q060 (test), same heading of ticket-creation, duplicate
check vs reminders to staff, different quotes. The eleven kin pairs of pass 1 are unchanged. No test
question is a rewording of a dev question.

## Pass 2: quota check

The stratification table holds unchanged (24/72/24; 60/60; 44 filtered, 22/22; section 30, product 14;
every article 2 dev / 2 test). Realism quotas on the new rows hold: q118 stays a fragment (5 words), q059
stays multi-part, no typo or short-form row was changed, so the pass 1 counts (14 / 10-13 / 28 / 16)
stand.

## Pass 2: summary

| Verdict | Count |
|---|---|
| ok | 120 |
| fix | 0 |
| remove | 0 |

No verdict is outstanding. SHA-256 of `data/gold.jsonl` at `4f3bd8f`:
`6141c7e3e1a42066128b5a0323c032a5f44d704e670c7f07f8a58cb79aa37a72`.
It equals the `data/gold.jsonl` entry in `data/MANIFEST.json`: yes.
