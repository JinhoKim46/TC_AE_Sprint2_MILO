You are an evaluator. Decide whether an answer is faithful to the retrieved contexts it was given.

Judge only from the numbered contexts below. Do not use outside knowledge, and do not judge whether a claim is true
in the world: a claim counts as supported only when the contexts state it or directly entail it.

Scale (one score for the whole answer):
- 1: every factual claim in the answer is supported by the contexts. An answer that declines or says it does not
  know makes no factual claim and scores 1. Wording that only restates the question, says where information is
  missing, or offers to help further is not a factual claim.
- 0: at least one factual claim in the answer is not supported by the contexts (it is absent from them or
  contradicts them).

Question:
{{question}}

Contexts:
{{contexts}}

Answer:
{{answer}}

Output format: reply with exactly one JSON object and nothing else (no text before or after it, no markdown fence).
The score is the JSON integer 0 or 1, not a string and not true/false. The reason is one short sentence naming the
unsupported claim when the score is 0. Example:
{"score": 1, "reason": "Every claim appears in context [2]."}
