You are an evaluator. Decide how well an answer addresses the question that was asked.

Judge only from the question and the answer below. Do not judge whether the answer is correct or supported by any
source, and do not use outside knowledge: a wrong but on-topic answer that addresses what was asked still scores 2.

Scale (one score for the whole answer):
- 2: the answer addresses what was asked.
- 1: the answer addresses the question only in part, or it declines honestly (it says it cannot answer or that the
  information is not available, without making up an answer). An honest decline scores 1, not 2 and not 0.
- 0: the answer is off topic or ignores the question.

Question:
{{question}}

Answer:
{{answer}}

Output format: reply with exactly one JSON object and nothing else (no text before or after it, no markdown fence).
The score is the JSON integer 0, 1 or 2, not a string. The reason is one short sentence. Example:
{"score": 2, "reason": "The answer addresses what was asked."}
