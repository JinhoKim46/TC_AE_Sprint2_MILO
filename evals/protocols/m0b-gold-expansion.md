# Protocol M0B-GOLD: gold set expansion to 120 questions

| Field | Value |
|---|---|
| Protocol id | `m0b-gold-expansion` |
| Status | committed before results |
| Date | 2026-10-22 |
| Owning milestone | M0b (gold expansion, data role; audit by the separate `audit` role) |
| Implements | SPEC D60, D78, D55, D69, D83; section 10.1; HARNESS rules 6, 7 and 14 |
| Produces | `data/source/gold_questions.yaml`, the gold rows of the gold file under `data/`, `data/MANIFEST.json`, a `generation-record.md` entry, `data/prompts/gold-audit.md` |
| Measured calls | none (0 USD) |

## Question

Can the 40-row gold set be grown to 120 realistic, auditable questions over the 24 knowledge-base
articles, split 60 dev and 60 test, so that the retrieval and chat comparisons (E01 rerun, E02) and
the M7 labels rest on a sample large enough to separate models, without any eval reading the test split?

## Data

Inputs, all in the repo: the 24 articles under `data/kb/`, `data/source/catalogue.yaml`,
`data/pricing/rate_card.v1.yaml`, the 40 committed rows of the gold file (kept, not rewritten),
the row format of SPEC section 3 and the kit template (fields `qid`, `question`, `type`,
`gold_evidence`, `reference_answer`, `filters`, `split`).

Starting point (committed file, checked by hand on 2026-10-22): 8 identifier (4 dev, 4 test),
24 paraphrase (12, 12), 8 unanswerable (4, 4); 11 rows with a filter (5 dev, 6 test).
`data/prompts/generation-record.md` says 8 filtered, 4 dev; the file is right and the record is
corrected in M0.

### Stratification table

D78 pins 48 answerable dev questions, so dev holds exactly 12 unanswerable. Test mirrors dev.

| Type | Now (dev/test) | Add (dev/test) | Final (dev/test) | Rule met |
|---|---|---|---|---|
| identifier | 8 (4/4) | 16 (8/8) | 24 (12/12) | at least 8 per split |
| paraphrase | 24 (12/12) | 48 (24/24) | 72 (36/36) | per-type split differs by at most 1 |
| unanswerable | 8 (4/4) | 16 (8/8) | 24 (12/12) | at least 12 per split |
| total | 40 (20/20) | 80 (40/40) | 120 (60/60) | D60 |
| with a filter | 11 (5/6) | 33 (17/16) | 44 (22/22) | at least 40, at least 20 dev |

Secondary quotas, on the 96 answerable rows: every article has at least 3 and at most 6 answerable
rows, with dev and test each holding at least 1 per article. Filters use the keys already in the
file, `section` and `product`, at least 12 filtered rows per key, and every filtered row's evidence
document satisfies the filter against its front matter. The third self-query field, `updated_after`,
gets no quota: all 24 articles carry `updated: 2026-10-01` (checked 2026-10-22), so such a filter is
either always true or always empty, and M0b does not edit front matter. Whether the kit checker
accepts any other filter key is to verify (the checker is not in the repo yet).

Realism quotas, on the 80 new rows, half in each split: at least 16 with a typo or a short form
("ph intake", "crm conn"), at least 10 multi-part questions whose parts are answered by one quote,
at least 10 fragments of five words or fewer, at least 8 written as an owner who answers in fragments
("just phone, spreadsheet, 35 a day. cost?"). These tags live in `gold_questions.yaml` only; they are
not written to the gold file, whose extra fields are unverified against the checker.

The three types above are the ones in the committed file. The kit's `GOLD_TYPES` constant is to verify
when `scripts/check_project_data.py` is copied at S0; a type outside that list is not used.

## Procedure

### Step 1. Generation (session agent, no model API)

A session Fable agent (the M0b data role) writes the 80 new rows into `data/source/gold_questions.yaml`
by reading the articles. It calls no model API and makes no network call (D55, D126, HARNESS rule 14).
`scripts/build_data.py` reads the YAML and writes the gold file and `data/MANIFEST.json`; nobody
edits the gold file by hand. This protocol never spells the gold file's name, so the section 10.1
leakage grep over `evals/` stays clean whatever scope the leakage test settles on.

Prompt contract for the generator (the brief it works under, recorded in `generation-record.md`):

1. One row per entry: `qid` (`q041` to `q120`, unique), `question`, `type`, `split`, `gold_evidence`
   (list of `{doc_id, quote}`; empty for unanswerable), `reference_answer`, `filters` (object or null),
   plus YAML-only tags `realism` (list from `typo`, `short_form`, `multi_part`, `fragment`,
   `fragmented_owner`, `plain`) and `section_heading` (the level-2 heading the quote sits under).
2. A quote is copied verbatim from the article body: exact substring, no normalisation, at most 150
   characters, inside one heading section (so a chunker cannot split it), and it alone supports the
   reference answer.
3. The question shares at most 20 percent of its words with the quote (the P3 rule), and identifier
   rows name a module or integration id from `catalogue.yaml`.
4. An unanswerable question is about a topic absent from every article (checked by the auditor with a
   search over `data/kb/`), is plausible for an owner, and is not a paraphrase of an answerable row.
   `reference_answer` is `INSUFFICIENT_CONTEXT`, `gold_evidence` is `[]`, `filters` is null.
5. No two questions equal after lower-casing and whitespace collapse, and no new question shares more
   than 60 percent of its word set with any other question in the file (Jaccard over lower-cased words).
6. Questions never mention the demo companies, owners or the attack files; no real company or person.
7. The split is assigned per row at generation time, by alternating within each (type, article) group,
   and is never moved afterwards except by an audit fix.
8. The generator records in `generation-record.md`: files written, the brief, sources read, the check
   command, and the statement that no model call was made.

### Step 2. Static checks (deterministic)

`uv run python scripts/build_data.py --check` asserts the counts in the table above (D78 done-line),
unique `qid`, the duplicate rules, verbatim quotes at most 150 characters, filters matching front
matter, and the manifest hash. `python scripts/check_project_data.py data` prints `OK`.
`tests/test_checker.py` and `tests/test_leakage.py` assert the same counts and pass. The generator
commits on `agent/data/m0b` only when all four pass.

### Step 3. Independent audit (a different session agent, HARNESS rule 7)

The audit role starts from the generation commit on its own branch `agent/audit/m0b`, reads SPEC,
this protocol, the articles and the gold file, and does not read the generator's notes or chat. It
writes only `data/prompts/gold-audit.md`. It audits all 120 rows, old and new (D60). Checklist per row:

| Check | What the auditor verifies |
|---|---|
| Answerability | The quote alone answers the question; an unanswerable topic is absent from every article (search by keyword and by synonym) |
| Label correctness | `type` fits (identifier names an id; paraphrase does not copy the quote); `filters` match the document's front matter; `split` is set |
| Quote integrity | Exact substring of the article body, inside one heading, at most 150 characters |
| Duplicates | No equal question after normalisation; no near-duplicate (word Jaccard above 0.6) in the whole file |
| Leakage between splits | No dev and test pair asks the same fact from the same quote, and no test question is a rewording of a dev question |
| Realism | Reads like an owner's message; typos and fragments are natural, not random; at least the quotas in the table hold |
| Safety and privacy | No real name, company, key or path; no D22 phrase |

Verdict vocabulary per row: `ok`, `fix` (with the reason and the proposed change) or `remove`. The
token `reject` is not used anywhere in the audit file, because the verifier greps for it (HARNESS 7.9).

### Step 4. Resolution

The auditor flags; the generator fixes in `gold_questions.yaml` on `agent/data/m0b`, reruns step 2,
and commits. The auditor then runs a second full audit of the changed rows and a spot check of 20
unchanged rows, and records the SHA-256 of the final gold file, which must equal `MANIFEST.json`.
A second-pass `fix` goes through the same cycle; at most three cycles, after which the milestone is
blocked (HARNESS section 4). The generator never edits the audit file; the auditor never edits data.

### Step 5. Seal

Only the generator and the auditor read test rows, and only inside this milestone. Every evaluation
loads rows through `evals/common.py`, which returns dev rows unless `MILO_FINAL=1` (section 10.1).
No E01 rerun, E02 run, M1 or M2 work starts before the audit commit exists on `agent/audit/m0b`; the
audit commit precedes every `agent/retrieval/*` and `agent/advanced-rag/*` commit (D78), and
`tests/test_leakage.py` is re-anchored on it. `evals/retrieval/compare_embeddings.py` is rewritten in
this milestone to load rows through `evals/common.py` (section 21, pre-S0 scripts).

## Metrics

This protocol produces data, not a model score. Its measurements are counts: the table above, the
realism quotas, the per-article coverage, the number of `fix` and `remove` verdicts per audit pass,
and the number of audit cycles. All are reported in the audit file and the M0b checkpoint block.

## Decision rule

M0b is done when: every static check passes; the final audit lists all 120 qids with verdict `ok` and
a "changed since first pass" column; the audit records the SHA-256 equal to `MANIFEST.json`; the audit
commit is on `agent/audit/m0b`, after the last generation commit; and no verdict is outstanding.
A row that cannot be fixed in three cycles is removed and replaced by a new row that goes through a
fresh audit; the counts in the table must still hold.

## Limits

- The gold file is readable by anyone with the repo. Sealing is a process rule (the loader, the
  leakage test, the access log), not encryption. The report says so (section 10.1).
- The generator and the auditor are both Claude agents. Independence comes from separate sessions,
  separate branches and the auditor not reading the generator's notes; it is not a human audit.
- Binary relevance against a single quote: a question with several good chunks still has one
  relevant chunk, so recall numbers in E01 are a lower bound.
- The 120-row size separates large gaps (about 0.1 nDCG@5) but not small ones; the E01 rerun
  protocol states its own power limit.
- The kit checker's accepted `type` and `filters` keys are to verify once the checker is in the repo.

## Measured-call budget

0 USD. Every step runs in the session on the Claude subscription (D126, HARNESS rule 14). No
OpenRouter, embedding or other network call is made; `MILO_LIVE` is never set; no key is read.
