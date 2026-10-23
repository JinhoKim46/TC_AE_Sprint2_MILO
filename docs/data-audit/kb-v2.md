# Data-v2 audit: knowledge base content

Protocol `data-v2`, section "Data 4. Knowledge-base content audit". Date 2026-10-23. Branch `agent/kb-audit` at
`main` (`c54ff2a`). No model, embedding or network call; no `.env` read; 0 USD. No article was edited (D166): every
finding below is a proposal, and the article hashes are unchanged.

## Method

Inputs read: the 24 articles under `data/kb/**`, `data/source/catalogue.yaml`, `data/pricing/rate_card.v1.yaml`,
the 60 dev gold rows through `evals.common.load_gold("dev")` (36 paraphrase, 12 identifier, 12 unanswerable), the
owner turns of the 11 dev conversations in `data/demo/conversations/splits.json` (A-1, B-1, E-1, C-3, C-4, D-3,
D-4, E-3, E-4, F-3, F-4), `protocols/discovery.v1.yaml`, `src/milo/prompts/*.md`, and, to read what an article
promises against what the software does, `src/milo/pricing/estimator.py` and `src/milo/security/taint.py`. No test
row or test conversation was read.

Checks, in order:

1. **Contradictions.** Every article read in full; each claim about a price, surcharge, setup fee, tier, timeline,
   integration, approval, status or data policy compared across articles. Both passages are cited by chunk id.
2. **Rate card and catalogue.** Every USD figure, factor, tier bound, module limit, version string, module id,
   name, includes/excludes list and integration list compared with `rate_card.v1.yaml` and `catalogue.yaml`.
3. **Coverage.** Each dev gold quote was located with the real chunker (`milo.rag.chunk.chunk_document`, 800/100,
   relevance as in `evals/retrieval/run_retrieval.py`: a chunk is relevant when it holds the quote after whitespace
   collapse and lower-casing, in the gold document). For each row I then looked for other chunks that answer the
   same question, since a sibling chunk with the answer counts as a miss. Each unanswerable topic was searched in
   all 24 articles and near-misses recorded. Owner questions in the dev conversations (turns with a question or a
   price, setup, data or urgency word) were matched to articles.
4. **Chunking.** Chunk lengths and section boundaries at 800/100, 400/50 and 1500/150; which chunks name their
   subject.
5. **Over-answering and taint.** Unqualified claims and implied promises flagged by reading; the seven D22 phrases
   checked with `taint.matched_phrases` on every article.
6. **Duplicates.** Passages that state the same fact in two articles or two chunks, recorded where they compete
   for a gold row or an owner question.

Hygiene results (no issue): every article has the five front-matter keys (`title`, `section`, `updated`, `doc_id`,
`product`), word counts 287 to 493 (inside 150 to 600), no demo company or owner name, and
`taint.matched_phrases` is empty for all 24 articles. Every rate-card figure matches: the six base prices, the
900 USD setup fee, the four surcharges, the tiers 20/60/150 with 1.00/1.25/1.50 and "a person prices it" above
150, the range factors 0.90/1.00/1.20, the 6-module maximum and the version `rate-card-v1-2026-10`. Every module's
includes, excludes and integration list matches the catalogue; the API (one working day) and CRM (two working
days) setup times match. All 48 dev gold quotes sit whole in one chunk of their gold document at 800/100.

Chunk facts that frame the retrieval findings: every section of every article is shorter than 800 characters
(the longest chunk is 691 characters), so at 800/100 one section is one chunk and 1500/150 yields the same chunks. A
chunk carries only its `##` heading, not the article title, and 33 of the 167 chunks never name their subject
(K09).

Limit worth knowing when reading later results: `src/milo/prompts/reply.md` forbids the assistant to state any
price, fee or amount, while the KB and the dev reference answers (q009, q012, q042, q044, q077) carry USD figures.
That is not a KB defect, but a judge that penalises a missing number on those rows is measuring the prompt rule,
not retrieval.

## Issue table

Columns: **quote** = the fix changes a gold quote's text (gold re-check and possibly a gold edit); **gold chunk** =
the fix changes the text of a chunk that holds a dev gold quote (quote still present, M0b re-check needed);
**index** = the fix changes chunk text, so `make index` and a rebuild; **ids** = the fix adds or removes a
paragraph or heading, so `doc#i` ids may shift. "gold" in the fix column means a change to `gold.jsonl`, not to
an article.

| id | sev | check | where | finding | proposed fix | quote | gold chunk | index | ids |
|---|---|---|---|---|---|---|---|---|---|
| K01 | high | contradiction, catalogue | `cases/trades-dispatch#3` vs `implementation/export-only-tools#3`, `catalogue.yaml` | The case recommends email-triage, ticket-creation and staff-assignment and says "the export-only-tool surcharge was shown". The catalogue and export-only-tools#3 offer export-only-tool for document-data-entry only. Second fault: the tool "could export data but could not import it" and has no API, so ticket-creation has no valid integration at all (csv-export needs import, `implementation/csv-export#2`). (q052) | In #3, add document-data-entry to the list ("...and document-data-entry to read the paper job sheets, because the field-service tool was export-only..."), keep the q052 clause byte-identical; in #4 state that tickets reach the office as a CSV file or a printed sheet, not the field-service tool. | no | yes (q052 in #3) | yes | no |
| K02 | high | contradiction, catalogue | `implementation/export-only-tools#6` vs `implementation/api-connector#3`, `catalogue.yaml` | "When you later move to a tool with an API, the integration changes to api-connector": api-connector is not offered for document-data-entry, the only module export-only-tool serves. #0 and #1 also frame the integration for "field-service and job tools" and "many trades businesses", which invites the K01 error. | "When you later move to a tool that can import CSV files, the integration changes to csv-export and the surcharge falls to 0 USD." Optionally add to #1 that the integration is offered for document-data-entry only. | no | no (q050 is #3, q069 is #2) | yes | no |
| K03 | high | contradiction, over-answer | `policies/personal-data#2` vs `#2` (own next sentences) and `#3` | "What we store about your customers: Nothing." The same chunk then says a pasted customer record stays until the operator deletes it, and #3 says an uploaded document's text is stored with the session even if it holds customer data. An assistant quoting "Nothing" makes a false data claim. (q096 quote in #2) | Replace "Nothing." with "No customer records are collected." and keep the rest; add to #3 "until it is removed". | no | yes (q096) | yes | no |
| K04 | medium | contradiction, catalogue | `cases/grooming-bookings#2`, `cases/accounts-paperwork#2` vs `implementation/csv-export#2`, `catalogue.yaml` (csv-export, export-only-tool) | "The booking widget could export CSV, so the csv-export integration was available": by the catalogue an export-only tool is export-only-tool, which phone-intake and customer-status-updates do not support; csv-export needs export and import. #4 ("fed by the CSV file") partly rescues it. The accounts case says only "could import CSV". | Grooming #2: "The booking widget could export and import CSV, so...". Accounts #2: "could export and import CSV files". | no | no (q084 is grooming #3; q086/q088 are accounts #4/#6) | yes | no |
| K05 | medium | contradiction, scope | `cases/trades-dispatch#4` vs `#3`, `solutions/email-triage#1`, `consultation/urgent-requests#3` | "The night line was routed to the on-call engineer through the urgent label": the night line is a phone line, no phone-intake was recommended, and the urgent label belongs to email-triage (mail). The passage implies email-triage routes phone calls. (q081) | Either "Night messages were labelled urgent and routed to the on-call engineer" (quote changes) or add phone-intake to #3 and leave #4 as is. | yes, or no with the #3 route | yes (q081) | yes | no |
| K06 | medium | catalogue, over-answer | `consultation/urgent-requests#3` vs `catalogue.yaml` (ticket-creation, staff-assignment includes) | Claims an "urgent flag" on tickets and a staff-assignment suggestion of "the on-call person"; neither is in the catalogue includes (staff-assignment uses skill tags and availability). "None of the modules decides on its own that something is not urgent" is an unqualified guarantee of perfect classification. (q056 quote in #3) | "...creates the ticket with an urgent request type, so that staff-assignment can suggest someone free with the matching skill." Qualify the last sentence: "Messages that match your definition are treated as urgent; anything unclear is passed to a person." | no | yes (q056) | yes | no |
| K07 | medium | contradiction (status rules) | `pricing/how-we-price#2` ("Nothing else changes the price"), `pricing/range-and-estimates#2` ("every pricing input was given by you") vs `consultation/volume-and-timing#5`, `pricing/setup-fees#4`; engine `estimator.py` | The KB never lists which answers decide the status. volume-and-timing#5 makes an undecided start a scenario, although start is not one of "the four inputs"; the engine also makes a converted (estimated) volume a scenario, which volume-tiers#4 and volume-and-timing#1 do not say. A reader of range#2 cannot predict the status. (q079 in range#2, q094 in v&t#5) | Add one sentence to range#2 after the table: "The fields that decide the status are the modules, the daily volume, the integrations and the start date; a volume converted from a weekly or monthly figure counts as an assumption." | no | yes (q079) | yes | no |
| K08 | medium | engine vs KB | `pricing/setup-fees#1`, `pricing/how-we-price#4`, all six `solutions/*#5` vs `estimator.py` docstring ("low, base and high apply the range factors to each of the two totals") | Every article states the setup fee as a fixed 900 USD per module, while the engine shows a 0.90 to 1.20 range on the setup total too (810 to 1,080 USD for one module). Not a rate-card mismatch (the card does not say what the range applies to), but an owner will see figures the KB does not explain. | Owner decision: either one sentence in range-and-estimates#1 ("The range applies to the monthly total and to the setup total") or an engine change so setup is shown fixed. | no | yes (q020 in range#1) if KB route | yes if KB route | no |
| K09 | medium | chunking, retrieval | 33 chunks: 4 per solution article (`#1` what it does, `#2` included, `#4` connecting, `#6` who it suits) and `api-connector#1,#2,#5`, `crm-connector#1,#2,#5`, `export-only-tools#1,#2,#5` | These chunks say "the module" or "setup takes" and never name the article's module or integration, and the six solution articles use the same section template, so e.g. the four "Connecting it to your tools" chunks are near-identical. 10 of 48 dev quotes sit in such chunks: q010, q024, q045, q048, q058, q061, q063, q067, q068, q069. q024 ("connect a customer database") must hit `crm-connector#2`, whose text has no "CRM" and competes with `api-connector#2` ("Setup takes one working day"). | Preferred, no KB edit: a chunker change that prefixes the article title to each chunk's embedded text (code request to the RAG owner; article hashes unchanged, quotes still contained). KB alternative: name the module in the first sentence of each of these sections. | no | yes (10 rows, either route) | yes | no |
| K10 | medium | coverage | `solutions/ticket-creation#1` vs gold `#2` (q010) | q010 "How are repeated submissions of one job prevented?" is answered better by #1 ("checks for duplicates, so the same request sent twice does not become two jobs") than by the gold list sentence in #2; a retriever that ranks #1 first scores a miss. | gold: add the #1 sentence as a second `gold_evidence` entry. | adds evidence | no | no | no |
| K11 | medium | duplicates, coverage | q003: `what-is-included#4`; q005: `accounts-paperwork#5`; q063: `accounts-paperwork#4`; q018: `api-connector#4`; q100: `security#4`; q027: `urgent-requests#6`; q104: `personal-data#4`; q090: `volume-and-timing#1`, `volume-tiers#4`; q056 and q058 cross-pair (`urgent-requests#3`, `email-triage#1`); q001 and q053: `grooming-bookings#5`, `phone-intake#3` | Each gold row's answer is also stated, in near-identical words, in another chunk (shown here); several of these rows have no filter, or a product filter that admits both chunks (q005, q063 with `document-data-entry`, q053 with `phone-intake`). Rank metrics then depend on which twin the retriever prefers. | gold: add the twin passage as alternative evidence where it fully answers the question (q003, q018, q100, q104, q090, q058); leave the case-specific rows (q053, q086) single. No KB edit. | adds evidence | no | no | no |
| K12 | medium | duplicates (figures) | base prices: `how-we-price#3` table vs each `solutions/*#5` (q009, q012 unfiltered); tiers: `volume-tiers#1` table vs `#2` (q075); csv 0 USD: `csv-export#4` vs `setup-fees#2`, `staff-assignment#5` (q007); "900 USD per module" in 10 chunks (q077 in `setup-fees#1`); "Setup takes one/two working days" `api-connector#2` vs `crm-connector#2` (q024) | The same figure appears in a table chunk and a prose chunk; tables are the likelier lexical match for a price question but never hold the gold quote. Consistent facts, so no answer is wrong, only rank metrics are noisy. | gold: add the table chunk (by a quoted row, e.g. "phone-intake | 690 USD") as alternative evidence for q009, q012, q075; leave q077 single. No KB edit. | adds evidence | no | no | no |
| K13 | medium | duplicates (owner questions) | conflict handling: `volume-and-timing#4`, `volume-tiers#4`, `trades-dispatch#2`; price asked early: `first-questions#7`, `range-and-estimates#3`; setup fee coverage: `setup-fees#1`, `what-is-included#2` | Three facts each stated twice or three times, consistently. Dev owners ask exactly these (price early: C-3 t3, D-4 t2, E-3 t3; setup fee cover: A-1 t23; conflicting volume: B-1 t9, D-4 t16, F-3 t4), so retrieval returns a mix and the reply may blend them. No gold row depends on the choice (q079 is range#2). | None now. If an edit round happens, keep the full text in one article and replace the twin with a one-line pointer. | no | no | yes if edited | no |
| K14 | medium | conversation gap | owners: C-3 t5 (text messages, web form), D-4 t12 (patient portal, front desk), A-1 (web form), F-4 (tracking app) | No article says which module, if any, handles text messages, web forms, portals or walk-ins; the solution articles cover phone and email only. | Add a short "Channels" paragraph to `consultation/first-questions` or a new article stating what is covered and that other channels are not. Interacts with unanswerable q105 (WhatsApp): re-check that it stays unanswerable. | no | no | yes | yes if new section |
| K15 | medium | conversation gap | D-3 t17 ("estimate for three sites"), C-3 t4, A-1 (two sites) | The interview asks the number of locations, but no article says whether sites change the price or whether one module covers several sites. how-we-price#2 ("Nothing else changes the price") implies it, but only by inference. | One sentence in `pricing/how-we-price#2`: "The number of sites does not change the price; the daily volume across all sites does." | no | yes (q070 in #2) | yes | no |
| K16 | medium | conversation gap | D-3 t14 (patients, post-surgery calls), E-4 t13 (allergy notes) | `policies/personal-data` covers contact data and customer records in general, with nothing on health or other sensitive data, which two dev owners hold. | One sentence in personal-data#2: the interview records only that such data is held, and the module agreement (#6) covers it. | no | yes (q096) | yes | no |
| K17 | low | conversation gap | E-3 t17 (invoicing as a goal), C-3 t7 (weekly invoices) | Only `ticket-creation#3` says "it does not do billing". No article says that no module invoices. | Optional line in `what-is-included#4`. | no | no | yes | no |
| K18 | low | conversation gap | C-3 t7 ("never text clients after 8pm") | `customer-status-updates` says nothing about send-time windows. | Optional line in customer-status-updates#1 only if the product supports it; otherwise leave the gap. | no | no | yes | no |
| K19 | medium | coverage (unanswerable), over-answer | q039 vs `setup-fees#1`, `what-is-included#2`, `api-connector#2` ("checking the first results with you"); q113 vs `phone-intake#1` ("slots you have opened"), `api-connector#0`; q117 vs `what-is-included#3` ("travel", "a device at your premises"), `api-connector#2` ("with you watching"); q107 vs `security#1` (password, lockout); q115 vs `security#7`, `what-is-included#5` | All 12 unanswerable topics are absent from every article, but five have adjacent text a model can stretch into an answer (training, calendar sync, on-site setup, 2FA, liability). | None to the KB (adding the facts would flip the rows). Note for the judge: these rows test abstention against near-miss context. | no | no | no | no |
| K20 | medium | hygiene (leakage) | `cases/grooming-bookings` vs company A and A-1; `cases/trades-dispatch` vs company B and B-1 | Names pass, but the cases mirror two dev companies closely: a two-site groomer with phone and web form, a booking widget with CSV export, missed calls, hand reminders and no-shows (A-1 t12); a heating firm with a shared inbox, an emergency line, no heat or gas smell, and a daily-vs-monthly volume conflict (B-1 t5, t9, t19). Retrieved case text reads like the owner's own facts ("the estimate was marked indicative"). | Defer: changing the cases touches six gold quotes (q052, q053, q081, q084 and the K04/K05 chunks). Record as a known confound of A-1 and B-1 results. | yes if edited | yes | yes | no |
| K21 | low | chunking, consistency | `solutions/*#5` (Price) vs `#4` (Connecting) | Price sections differ on surcharges: customer-status-updates gives 300 USD, email-triage says "a monthly surcharge applies" with no figure, phone-intake omits it, ticket-creation gives only the 450 CRM surcharge, document-data-entry omits csv 0 USD. A question like "phone-intake with the API, what per month?" needs #4, #5 and setup-fees#2. | Uniform Price paragraph naming each supported integration and its surcharge; keep each first sentence (the gold quote) unchanged. | no | yes (q009, q012, q042, q044) | yes | no |
| K22 | low | over-answer | `what-is-included#0` ("no surprises after the first invoice"), `#1` ("you can ask for them at any time"), `setup-fees#5` ("Nothing hidden", "no other fees") vs `what-is-included#3` (out-of-scope work quoted as additional) | Implied promises; "no other fees" sits beside a passage that names extra paid work. | Heading "What else you may pay for"; "Minor changes ... are included." without "at any time" (changes q072's quote). | yes (q072, if #1 edited) | yes | yes | no |
| K23 | low | over-answer | `policies/security#3` | "neither can be changed by anything written in a message" is absolute while taint is a lexical rule (security report, D22 limit); #7 qualifies it only generally. | "...and the software does not let a message change either." | no | no (q104 is #2) | yes | no |
| K24 | low | contradiction (minor) | `cases/trades-dispatch#5` vs `protocols/discovery.v1.yaml` (`budget_range` required_for []) | "The estimate was a scenario, because the owner had not decided when to start, and the budget was recorded as unknown" reads as if budget made it a scenario; budget gates nothing and volume-and-timing#6 says it never sets the price. | "...when to start. The budget was recorded as unknown." | no | no | yes | no |
| K25 | low | engine vs KB | `pricing/range-and-estimates#2` vs `estimator.py` | The KB lists three statuses; the engine also has `unavailable` (assessment incomplete or volume above 150). volume-tiers#3 covers the second case in words. | Optional row "no estimate yet" in the status table. | no | yes (q079) | yes | no |
| K26 | low | catalogue | `implementation/csv-export` title "CSV export and import" vs catalogue name "CSV export or import" | Name differs by one word. | Align either side. | no | no | yes (front matter not chunked; heading is) | no |
| K27 | low | chunking | `cases/trades-dispatch#3` | The "What was recommended" paragraph is longer than 400 characters, so at 400/50 the q052 quote is split across windows; at 800/100 and 1500/150 it is whole. Only matters if the chunk size changes. | None; K01's edit should keep the paragraph under 800 characters. | no | (K01) | (K01) | no |

## Summary counts

| severity | count | ids |
|---|---|---|
| high | 3 | K01, K02, K03 |
| medium | 15 | K04 to K16, K19, K20 |
| low | 9 | K17, K18, K21 to K27 |
| total | 27 | |

By fix route: 5 issues are gold-only (K10, K11, K12 add alternative evidence; K19 and K20 notes) and need no index
rebuild; 1 is a code request (K09, chunker title prefix); the rest are KB edits. Of the three high issues, two sit
in gold-bearing chunks (K01 `trades-dispatch#3`, q052; K03 `personal-data#2`, q096), and all three can be fixed
without changing a gold quote's text.

## Recommendation

Do one targeted KB edit round before the Haiku baseline, limited to K01, K02, K03 and K04 (four articles,
sentence-level, no new paragraphs or headings, so no `doc#i` shift) and optionally K05 by the "add phone-intake to
#3" route so q081's quote stays. Reasons: K01 and K03 are wrong facts inside chunks that dev gold rows retrieve, so
a baseline on the current KB would have to be rerun after any later fix; the rebuild itself is cheap (the last
`make index` cost about 0.002 USD, ledger row F2-1); and with the quotes kept byte-identical the gold re-check is a
mechanical containment check. The real cost is that the edit voids stored E01 and M7 contexts, which is the owner's
call (D166). Handle the retrieval ambiguity (K09 to K12) through alternative gold evidence and the chunker title
prefix, not through KB text. Defer the gaps (K14 to K18), K20 and the low items to a later round; none of them
makes an answer wrong.

## Applied (2026-10-23)

Owner chose option A. K01 to K04 fixed, K10 to K12 handled as accepted alternative passages, K09 (title prefix)
measured and not adopted: `evals/protocols/kb-edit-v1.md`, SPEC D168, `docs/rag-parameters.md`. The rest stay open.
