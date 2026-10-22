# Reflection

Status: skeleton. The sections below are filled after the live batch and the M7 judge result. Each heading under
"Limits" is a limit already known; the text under it is written when the number or the evidence exists.

## What the course asked for and what was built

Pending the final report.

## What the measurements say

Pending live run: retrieval (M1), advanced arms (M2), tools (M3), attacks (M4), chat model comparison (E02), judge
agreement (M7).

## Limits

### Sample size

30 labelled questions (6 of them unanswerable) and 60 dev questions in all: wide intervals; kappa thresholds are
absolute. Pending live run: the observed interval widths.

### Single labeller

The labels come from one labelling agent (a model session), so kappa measures agreement with one labeller and not
with a human standard. Pending: whether a second labeller was added.

### Label and judge order

Labels were written after the product answers were fixed; judge prompts were written after the labels and may fit
them. Pending: the commit ids that show the order, and whether any prompt changed after the first judge run.

### Prevalence and undefined kappa

Faithfulness labels are expected to be mostly 1, which lowers kappa for a given agreement. Pending live run: label
prevalence per metric and any metric whose kappa is undefined or flagged low prevalence.

### Judge and product models

Two judge models, chosen from the E02 candidates, one run each at temperature 0; live reruns are reported, not
failed (SPEC 10.3). Pending live run: slugs, and whether the two judges agree with each other.

### Retrieval configuration

The judged answers come from the dense contract-mode pipeline unless an M2 arm was adopted. Pending: which
configuration was judged.

### Dev only

Everything here is on the dev split; the test split is read once, at F2.

### Stand-in prompts and fixtures

Dry results come from an invented fixture and carry no MILO information.
